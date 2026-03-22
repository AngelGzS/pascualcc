"""PO3 entry detection on 15m chart within the 10AM-2PM EST window (Step 2)."""
from __future__ import annotations

import logging

import pandas as pd

from strategies.po3 import settings
from strategies.po3.detector_cisd import detect_cisd
from strategies.po3.detector_fvg import detect_fvgs, update_fvg_fills
from strategies.po3.detector_sweep import detect_sweeps
from strategies.po3.types import CISD, FVG, LiquiditySweep, PO3Signal

logger = logging.getLogger(__name__)


def find_entry(
    df_15m: pd.DataFrame,
    window_start_idx: int,
    window_end_idx: int,
    bias_direction: str,
    htf_fvg: FVG,
    symbol: str,
) -> PO3Signal | None:
    """Find an entry within the 10AM-2PM EST window on 15m chart.

    Steps:
        1. Detect all FVGs in the 15m data up to window_start.
        2. Iterate candle by candle within the window:
           a. Update FVG fill status.
           b. Look for manipulation sweep in opposite direction.
              - LONG bias: look for LOW sweep (price dips below swing low).
              - SHORT bias: look for HIGH sweep (price spikes above swing high).
           c. After sweep found, look for CISD in bias direction.
           d. If CISD found: generate signal with entry/SL/TP levels.

    Args:
        df_15m: Full 15m OHLCV DataFrame.
        window_start_idx: Start bar index of the 10AM-2PM window.
        window_end_idx: End bar index of the window.
        bias_direction: 'long' or 'short'.
        htf_fvg: Higher-timeframe FVG that validated the bias.
        symbol: Trading symbol.

    Returns:
        PO3Signal if entry found, None otherwise.
    """
    # Detect existing FVGs from data leading up to the window
    pre_window = df_15m.iloc[: window_start_idx + 1]
    fvgs_15m = detect_fvgs(pre_window, "15m")

    # Determine which sweep type to look for
    target_sweep = "low_sweep" if bias_direction == "long" else "high_sweep"
    cisd_direction = "bullish" if bias_direction == "long" else "bearish"

    sweep_found: LiquiditySweep | None = None

    for i in range(window_start_idx, min(window_end_idx + 1, len(df_15m))):
        candle = df_15m.iloc[i]

        # Update FVG fill status with current candle
        update_fvg_fills(fvgs_15m, candle["high"], candle["low"])

        # Detect new FVGs as data progresses
        if i >= 2:
            triplet = df_15m.iloc[i - 2 : i + 1].reset_index(drop=True)
            new_fvgs = detect_fvgs(triplet, "15m")
            for fvg in new_fvgs:
                fvg.bar_index = i - 1  # middle candle in original index
            fvgs_15m.extend(new_fvgs)

        # Look for manipulation sweep if not yet found
        if sweep_found is None:
            sweeps = detect_sweeps(df_15m, i)
            for s in sweeps:
                if s.sweep_type == target_sweep:
                    sweep_found = s
                    logger.debug(
                        "Manipulation sweep found: %s at bar %d, level=%.2f",
                        s.sweep_type,
                        i,
                        s.level_swept,
                    )
                    break
            continue  # After finding sweep, wait for next candle for CISD

        # Sweep found — now look for CISD in bias direction
        cisd = detect_cisd(df_15m, sweep_found.bar_index + 1, cisd_direction)
        if cisd is not None and cisd.bar_index <= window_end_idx:
            levels = _calculate_levels(bias_direction, cisd.fvg)
            bias_type = (
                "low_sweep_long" if bias_direction == "long" else "high_sweep_short"
            )

            signal = PO3Signal(
                timestamp=cisd.timestamp,
                symbol=symbol,
                direction=bias_direction,
                bias_type=bias_type,
                htf_fvg=htf_fvg,
                entry_fvg=cisd.fvg,
                sweep=sweep_found,
                cisd=cisd,
                entry_price=levels["entry"],
                stop_loss=levels["stop_loss"],
                take_profit_2r=levels["tp_2r"],
                take_profit_3r=levels["tp_3r"],
                risk_points=levels["risk"],
            )
            logger.info(
                "PO3 entry found: %s %s | entry=%.2f SL=%.2f TP2=%.2f TP3=%.2f",
                bias_direction.upper(),
                symbol,
                signal.entry_price,
                signal.stop_loss,
                signal.take_profit_2r,
                signal.take_profit_3r,
            )
            return signal

    return None


def _calculate_levels(direction: str, entry_fvg: FVG) -> dict:
    """Calculate entry, SL, and TP levels from the entry FVG.

    Long:  entry = FVG midpoint, SL = FVG bottom,
           TP = entry + (entry - SL) * RR.
    Short: entry = FVG midpoint, SL = FVG top,
           TP = entry - (SL - entry) * RR.
    """
    entry = entry_fvg.midpoint

    if direction == "long":
        stop_loss = entry_fvg.bottom
        risk = entry - stop_loss
        tp_2r = entry + risk * settings.DEFAULT_RR
        tp_3r = entry + risk * settings.MAX_RR
    else:
        stop_loss = entry_fvg.top
        risk = stop_loss - entry
        tp_2r = entry - risk * settings.DEFAULT_RR
        tp_3r = entry - risk * settings.MAX_RR

    return {
        "entry": entry,
        "stop_loss": stop_loss,
        "risk": risk,
        "tp_2r": tp_2r,
        "tp_3r": tp_3r,
    }
