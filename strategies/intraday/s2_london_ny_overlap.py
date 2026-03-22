"""Strategy 2: London-NY Session Overlap Momentum.

Rules:
- During London-NY overlap (8:00-11:00 EST), look for strong momentum.
- Determine trend from London session (3:00-8:00 EST): if close > open = bullish.
- During overlap, enter on the first 15m candle that:
  a) Has body > 1.5x average body of last 20 candles (displacement)
  b) Agrees with London trend direction
- Entry: market at candle close
- SL: opposite end of the displacement candle body + 0.1% buffer
- TP: 2R
- Max 1 trade per day.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.multi_strategy import SimpleSignal
from strategies.intraday.session_utils import add_est_columns, get_session_candles

STRATEGY_NAME = "London-NY Overlap Momentum"

LONDON_START = 3
LONDON_END = 8
OVERLAP_START = 8
OVERLAP_END = 11
BODY_MULT = 1.5
BODY_LOOKBACK = 20
SL_BUFFER = 0.001  # 0.1% buffer
TP_RR = 2.0


def generate_signals(df: pd.DataFrame, symbol: str) -> list[SimpleSignal]:
    df = add_est_columns(df)
    signals: list[SimpleSignal] = []
    traded_days: set[str] = set()
    all_days = sorted(df["tday"].unique())

    bodies = (df["close"] - df["open"]).abs().values

    for day in all_days:
        if day in traded_days:
            continue

        # Determine London session trend
        london = get_session_candles(df, day, LONDON_START, LONDON_END)
        if len(london) < 4:
            continue

        london_open = float(london["open"].iloc[0])
        london_close = float(london["close"].iloc[-1])
        if london_close > london_open:
            trend = "long"
        elif london_close < london_open:
            trend = "short"
        else:
            continue

        # Scan overlap window
        overlap = get_session_candles(df, day, OVERLAP_START, OVERLAP_END)

        for idx_pos, (idx, row) in enumerate(overlap.iterrows()):
            if day in traded_days:
                break

            idx_int = int(idx)
            body = abs(float(row["close"]) - float(row["open"]))

            # Average body of last N candles
            start_b = max(0, idx_int - BODY_LOOKBACK)
            avg_body = float(bodies[start_b:idx_int].mean()) if idx_int > start_b else 0.0
            if avg_body <= 0:
                continue

            # Check displacement
            if body < avg_body * BODY_MULT:
                continue

            close = float(row["close"])
            open_ = float(row["open"])
            high = float(row["high"])
            low = float(row["low"])

            # Must agree with London trend
            if trend == "long" and close <= open_:
                continue
            if trend == "short" and close >= open_:
                continue

            price = close
            if trend == "long":
                sl = low * (1 - SL_BUFFER)
                risk = price - sl
                if risk <= 0:
                    continue
                tp = price + risk * TP_RR
            else:
                sl = high * (1 + SL_BUFFER)
                risk = sl - price
                if risk <= 0:
                    continue
                tp = price - risk * TP_RR

            signals.append(SimpleSignal(
                timestamp=int(row["timestamp"]),
                bar_index=idx_int,
                symbol=symbol,
                direction=trend,
                entry_price=price,
                stop_loss=sl,
                take_profit=tp,
                strategy_name=STRATEGY_NAME,
                entry_type="market",
                metadata=f"london_trend={trend} body={body:.2f} avg={avg_body:.2f}",
            ))
            traded_days.add(day)
            break

    return signals
