"""PO3 strategy engine — orchestrates bias determination and entry detection."""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from strategies.po3 import settings
from strategies.po3.bias import determine_bias
from strategies.po3.detector_fvg import detect_fvgs, update_fvg_fills
from strategies.po3.entry import find_entry
from strategies.po3.session import (
    get_candle_est_hour,
    get_trading_day,
    is_weekday,
    resample_to_4h_est,
)
from strategies.po3.types import PO3Signal

logger = logging.getLogger(__name__)


class PO3Engine:
    """ICT Power of 3 strategy engine.

    Processes multi-timeframe data (4H + 15m) to generate PO3 signals.
    """

    def __init__(self, symbol: str):
        self.symbol = symbol

    def run(self, df_15m: pd.DataFrame) -> list[PO3Signal]:
        """Process 15m data and return all PO3 signals found.

        Algorithm:
            1. Resample 15m -> 4H (EST-aligned).
            2. Detect HTF FVGs on 4H data.
            3. For each trading day:
               a. Find the 6AM 4H candle -> determine_bias().
               b. If bias found, get the 15m candles for the 10AM-2PM window.
               c. find_entry() on those candles.
            4. Return all signals.

        Args:
            df_15m: 15-minute OHLCV DataFrame with 'timestamp' column (Unix ms).

        Returns:
            List of PO3Signal objects for all valid setups found.
        """
        if df_15m.empty or len(df_15m) < 20:
            logger.warning("Insufficient 15m data (%d rows) for PO3 analysis", len(df_15m))
            return []

        signals: list[PO3Signal] = []

        # Step 1: resample to 4H
        df_4h = resample_to_4h_est(df_15m)
        if df_4h.empty:
            return signals

        # Step 2: detect HTF FVGs
        df_4h.attrs["timeframe"] = "4h"
        htf_fvgs = detect_fvgs(df_4h, "4h")
        logger.info("Detected %d HTF FVGs on 4H data", len(htf_fvgs))

        # Step 3: process each trading day
        processed_days: set[date] = set()

        for bar_idx in range(1, len(df_4h)):
            ts = int(df_4h.iloc[bar_idx]["timestamp"])

            # Only trade weekdays
            if settings.TRADE_WEEKDAYS_ONLY and not is_weekday(ts):
                continue

            # Only process 6AM candles for bias
            if get_candle_est_hour(ts) != settings.BIAS_CANDLE_HOUR:
                continue

            trading_day = get_trading_day(ts)
            if trading_day in processed_days:
                continue
            processed_days.add(trading_day)

            # Update HTF FVG fills with all candles up to current
            candle = df_4h.iloc[bar_idx]
            update_fvg_fills(htf_fvgs, candle["high"], candle["low"])

            # Step 3a: determine bias
            active_fvgs = [f for f in htf_fvgs if not f.filled]
            bias_result = determine_bias(df_4h, bar_idx, active_fvgs)
            if bias_result is None:
                logger.debug("No bias for %s", trading_day)
                continue

            bias_direction, htf_fvg = bias_result
            logger.info("Bias for %s: %s", trading_day, bias_direction.upper())

            # Step 3b: find 15m window for 10AM-2PM EST
            window_start, window_end = self._find_15m_window(df_15m, trading_day)
            if window_start is None:
                logger.debug("No 15m data for entry window on %s", trading_day)
                continue

            # Enforce max daily trades
            day_signals = [s for s in signals if get_trading_day(s.timestamp) == trading_day]
            if len(day_signals) >= settings.MAX_DAILY_TRADES:
                logger.debug("Max daily trades reached for %s", trading_day)
                continue

            # Step 3c: find entry
            signal = find_entry(
                df_15m, window_start, window_end, bias_direction, htf_fvg, self.symbol
            )
            if signal is not None:
                signals.append(signal)
                logger.info(
                    "PO3 signal generated for %s: %s @ %.2f",
                    trading_day,
                    signal.direction.upper(),
                    signal.entry_price,
                )

        logger.info("PO3 engine complete: %d signals found", len(signals))
        return signals

    def _find_15m_window(
        self, df_15m: pd.DataFrame, trading_day: date
    ) -> tuple[int | None, int | None]:
        """Find the 15m bar indices for the 10AM-2PM EST entry window.

        Returns:
            (start_index, end_index) or (None, None) if no data available.
        """
        start_idx: int | None = None
        end_idx: int | None = None

        for i in range(len(df_15m)):
            ts = int(df_15m.iloc[i]["timestamp"])
            if get_trading_day(ts) != trading_day:
                continue

            hour = get_candle_est_hour(ts)
            if settings.ENTRY_WINDOW_START <= hour < settings.ENTRY_WINDOW_END:
                if start_idx is None:
                    start_idx = i
                end_idx = i

        return start_idx, end_idx
