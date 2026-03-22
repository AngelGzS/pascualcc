"""Signal engine: combines divergences, BOS, and trend context into Signal objects."""
from __future__ import annotations

import logging
from collections import defaultdict

import numpy as np
import pandas as pd

from config import settings
from config.types import Signal
from signals.divergence import detect_divergences, Divergence
from signals.structure import detect_bos, get_trend_context, get_ema_alignment

logger = logging.getLogger(__name__)


class SignalEngine:
    """Generates trading signals by combining divergences across indicators with BOS."""

    def __init__(
        self,
        pivot_left: int = settings.PIVOT_LEFT,
        pivot_right: int = settings.PIVOT_RIGHT,
        min_distance: int = settings.MIN_PIVOT_DISTANCE,
        max_distance: int = settings.MAX_PIVOT_DISTANCE,
        cooldown: int = settings.SIGNAL_COOLDOWN,
    ) -> None:
        self.pivot_left = pivot_left
        self.pivot_right = pivot_right
        self.min_distance = min_distance
        self.max_distance = max_distance
        self.cooldown = cooldown

    def generate_signals(self, df: pd.DataFrame, pair: str) -> list[Signal]:
        """Generate all signals for a DataFrame with indicators already computed.

        Applies pre-confluence filters (warmup, ATR percentile, cooldown).
        """
        if len(df) < settings.WARMUP_CANDLES:
            logger.warning("Not enough data for warmup: %d < %d", len(df), settings.WARMUP_CANDLES)
            return []

        # Detect BOS
        bos_series = detect_bos(df, left=self.pivot_left, right=self.pivot_right)

        # Detect divergences for each indicator
        indicator_configs = [
            ("rsi", "rsi"),
            ("mfi", "mfi"),
            ("tsi", "tsi"),
        ]

        all_divergences: list[Divergence] = []
        for col, name in indicator_configs:
            if col not in df.columns:
                continue
            divs = detect_divergences(
                df, col, name,
                left=self.pivot_left,
                right=self.pivot_right,
                min_distance=self.min_distance,
                max_distance=self.max_distance,
            )
            all_divergences.extend(divs)

        # Group divergences by bar_index and type direction
        bar_divs: dict[int, dict[str, list[Divergence]]] = defaultdict(lambda: defaultdict(list))
        for div in all_divergences:
            bar_divs[div.bar_index][div.div_type].append(div)

        # ATR percentile for filtering
        atr_series = df["atr"]
        valid_atr = atr_series.dropna()
        atr_percentile_10 = valid_atr.quantile(settings.ATR_LOW_PERCENTILE / 100.0) if len(valid_atr) > 0 else 0

        # Build signals
        signals: list[Signal] = []
        last_signal_bar: dict[str, int] = {}  # direction -> last bar index

        for bar_idx in sorted(bar_divs.keys()):
            if bar_idx < settings.WARMUP_CANDLES:
                continue

            for div_type, divs in bar_divs[bar_idx].items():
                direction = "long" if "bullish" in div_type else "short"

                # Cooldown filter
                key = f"{direction}_{div_type}"
                if key in last_signal_bar and (bar_idx - last_signal_bar[key]) < self.cooldown:
                    continue

                # ATR filter
                atr_val = atr_series.iloc[bar_idx]
                if pd.isna(atr_val) or atr_val < atr_percentile_10:
                    continue

                # Trend context
                trend_ctx = get_trend_context(df, bar_idx)

                # Pre-confluence filter: contra-trend without BOS
                recent_bos = bos_series.iloc[max(0, bar_idx - 10): bar_idx + 1]
                bos_dir = f"{direction[0:4]}ish_bos"  # 'bullish_bos' or 'bearish_bos'
                # DECISION: check last 10 bars for BOS in same direction
                has_bos = (recent_bos == bos_dir).any()

                if direction == "long" and trend_ctx == "bearish" and not has_bos:
                    continue
                if direction == "short" and trend_ctx == "bullish" and not has_bos:
                    continue

                # EMA alignment
                ema_align = get_ema_alignment(df, bar_idx, direction)

                # Collect which indicators detected divergence
                indicator_names = list({d.indicator for d in divs})

                rsi_val = df["rsi"].iloc[bar_idx] if "rsi" in df.columns else 0.0
                mfi_val = df["mfi"].iloc[bar_idx] if "mfi" in df.columns else 0.0
                tsi_val = df["tsi"].iloc[bar_idx] if "tsi" in df.columns else 0.0

                if pd.isna(rsi_val):
                    rsi_val = 0.0
                if pd.isna(mfi_val):
                    mfi_val = 0.0
                if pd.isna(tsi_val):
                    tsi_val = 0.0

                signal = Signal(
                    timestamp=int(df["timestamp"].iloc[bar_idx]),
                    pair=pair,
                    direction=direction,
                    signal_type=div_type,
                    divergence_indicators=indicator_names,
                    bos_confirmed=bool(has_bos),
                    trend_context=trend_ctx,
                    ema_alignment=ema_align,
                    price_at_signal=float(df["close"].iloc[bar_idx]),
                    atr_at_signal=float(atr_val),
                    rsi_value=float(rsi_val),
                    mfi_value=float(mfi_val),
                    tsi_value=float(tsi_val),
                )

                signals.append(signal)
                last_signal_bar[key] = bar_idx

        logger.info("Generated %d signals for %s", len(signals), pair)
        return signals
