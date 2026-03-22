"""Strategy 6: ORB Tight — first 15min candle only, with volume confirmation.

Rules:
- Opening Range = just the first 15m candle (9:30-9:45 EST)
- LONG: next candle closes above OR high + volume > average
- SHORT: next candle closes below OR low + volume > average
- SL: opposite end of OR candle
- TP: 3R
- Filters: min OR range 0.05%, max 1.5%
- Max 1 trade per day
- Only trade 9:45-12:00 EST
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.multi_strategy import SimpleSignal
from strategies.intraday.session_utils import add_est_columns

STRATEGY_NAME = "ORB Tight (15m candle)"

OR_MIN = 570       # 9:30
OR_END_MIN = 585   # 9:45
ENTRY_END_HOUR = 12
TP_RR = 3.0
VOL_LOOKBACK = 20


def generate_signals(df: pd.DataFrame, symbol: str) -> list[SimpleSignal]:
    df = add_est_columns(df)
    signals: list[SimpleSignal] = []
    traded_days: set[str] = set()
    all_days = sorted(df["tday"].unique())

    volumes = df["volume"].values

    for day in all_days:
        if day in traded_days:
            continue

        # First candle (9:30)
        or_mask = (df["tday"] == day) & (df["est_min"] == OR_MIN)
        or_candles = df[or_mask]
        if len(or_candles) == 0:
            continue

        or_row = or_candles.iloc[0]
        or_idx = int(or_candles.index[0])
        or_high = float(or_row["high"])
        or_low = float(or_row["low"])
        or_range = or_high - or_low
        mid = (or_high + or_low) / 2

        if or_range <= 0:
            continue

        # Min/max range filter
        range_pct = or_range / mid
        if range_pct < 0.0005 or range_pct > 0.015:
            continue

        # Avg volume
        start_v = max(0, or_idx - VOL_LOOKBACK)
        avg_vol = float(volumes[start_v:or_idx].mean()) if or_idx > start_v else 0.0

        # Scan next candles (9:45-12:00)
        entry_mask = (
            (df["tday"] == day)
            & (df["est_min"] > OR_MIN)
            & (df["est_hour"] < ENTRY_END_HOUR)
        )
        entry_candles = df[entry_mask]

        for idx, row in entry_candles.iterrows():
            if day in traded_days:
                break

            idx_int = int(idx)
            close = float(row["close"])
            high = float(row["high"])
            low = float(row["low"])
            vol = float(row["volume"])

            # Volume confirmation (optional, skip if no volume data)
            vol_ok = avg_vol <= 0 or vol >= avg_vol * 0.8

            # LONG
            if close > or_high and vol_ok:
                sl = or_low
                risk = close - sl
                if risk <= 0:
                    continue
                tp = close + risk * TP_RR

                signals.append(SimpleSignal(
                    timestamp=int(row["timestamp"]),
                    bar_index=idx_int,
                    symbol=symbol,
                    direction="long",
                    entry_price=close,
                    stop_loss=sl,
                    take_profit=tp,
                    strategy_name=STRATEGY_NAME,
                    entry_type="market",
                ))
                traded_days.add(day)
                break

            # SHORT
            if close < or_low and vol_ok:
                sl = or_high
                risk = sl - close
                if risk <= 0:
                    continue
                tp = close - risk * TP_RR

                signals.append(SimpleSignal(
                    timestamp=int(row["timestamp"]),
                    bar_index=idx_int,
                    symbol=symbol,
                    direction="short",
                    entry_price=close,
                    stop_loss=sl,
                    take_profit=tp,
                    strategy_name=STRATEGY_NAME,
                    entry_type="market",
                ))
                traded_days.add(day)
                break

    return signals
