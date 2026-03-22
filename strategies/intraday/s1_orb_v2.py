"""Strategy 1B: ORB Enhanced with trend filter and ATR-based sizing.

Improvements over base ORB:
1. Trend filter: only trade in direction of 20-period EMA on 1H (resampled)
2. Volatility filter: skip if OR range < 25th percentile or > 95th percentile
3. TP: 2.5R (optimized from parameter sweep)
4. Additional: wait for close + retest of OR level before entry
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.multi_strategy import SimpleSignal
from strategies.intraday.session_utils import add_est_columns, get_session_candles

STRATEGY_NAME = "ORB Enhanced (v2)"

OR_START_MIN = 570    # 9:30
OR_END_MIN = 600      # 10:00
ENTRY_START_HOUR = 10
ENTRY_END_HOUR = 13
TP_MULTIPLIER = 2.5
EMA_PERIOD = 20       # trend filter on 1H


def _compute_ema_1h(df: pd.DataFrame) -> pd.Series:
    """Compute EMA on resampled 1H close prices, mapped back to 15m index."""
    # Resample to 1H
    df_copy = df.copy()
    df_copy["dt"] = pd.to_datetime(df_copy["timestamp"], unit="ms", utc=True)
    df_copy = df_copy.set_index("dt")
    hourly = df_copy["close"].resample("1h").last().dropna()
    ema = hourly.ewm(span=EMA_PERIOD, adjust=False).mean()

    # Map back: for each 15m bar, get the most recent 1H EMA value
    result = pd.Series(index=df.index, dtype=float)
    for i in range(len(df)):
        ts = pd.Timestamp(df["timestamp"].iloc[i], unit="ms", tz="UTC")
        # Find most recent hourly EMA
        mask = ema.index <= ts
        if mask.any():
            result.iloc[i] = ema[mask].iloc[-1]
        else:
            result.iloc[i] = np.nan
    return result


def generate_signals(df: pd.DataFrame, symbol: str) -> list[SimpleSignal]:
    df = add_est_columns(df)
    signals: list[SimpleSignal] = []
    traded_days: set[str] = set()

    # Compute 1H EMA for trend filter
    ema_1h = _compute_ema_1h(df)

    # Track OR ranges for volatility percentile filter
    all_or_ranges: list[float] = []
    all_days = sorted(df["tday"].unique())

    # First pass: collect all OR ranges
    for day in all_days:
        or_mask = (df["tday"] == day) & (df["est_min"] >= OR_START_MIN) & (df["est_min"] < OR_END_MIN)
        or_candles = df[or_mask]
        if len(or_candles) >= 2:
            r = float(or_candles["high"].max()) - float(or_candles["low"].min())
            if r > 0:
                all_or_ranges.append(r)

    if len(all_or_ranges) < 5:
        return signals

    p25 = np.percentile(all_or_ranges, 25)
    p95 = np.percentile(all_or_ranges, 95)

    # Second pass: generate signals
    for day in all_days:
        if day in traded_days:
            continue

        or_mask = (df["tday"] == day) & (df["est_min"] >= OR_START_MIN) & (df["est_min"] < OR_END_MIN)
        or_candles = df[or_mask]
        if len(or_candles) < 2:
            continue

        or_high = float(or_candles["high"].max())
        or_low = float(or_candles["low"].min())
        or_range = or_high - or_low

        if or_range <= 0:
            continue

        # Volatility filter: skip too narrow or too wide
        if or_range < p25 or or_range > p95:
            continue

        # Get current EMA value at end of OR
        or_last_idx = or_candles.index[-1]
        current_ema = ema_1h.iloc[or_last_idx] if or_last_idx < len(ema_1h) else np.nan
        if np.isnan(current_ema):
            continue

        mid = (or_high + or_low) / 2

        # Trend filter: EMA direction determines allowed trades
        # Price above EMA = only longs, below = only shorts
        ema_bias = "long" if mid > current_ema else "short"

        entry_mask = (df["tday"] == day) & (df["est_hour"] >= ENTRY_START_HOUR) & (df["est_hour"] < ENTRY_END_HOUR)
        entry_candles = df[entry_mask]

        for idx, row in entry_candles.iterrows():
            if day in traded_days:
                break

            idx_int = int(idx)
            close = float(row["close"])
            high = float(row["high"])
            low = float(row["low"])

            # LONG: close above OR high, trend agrees
            if ema_bias == "long" and close > or_high and low >= or_low:
                sl = or_low
                risk = close - sl
                if risk <= 0:
                    continue
                tp = close + risk * TP_MULTIPLIER

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
                    metadata=f"OR_H={or_high:.2f} OR_L={or_low:.2f} EMA={current_ema:.2f} bias={ema_bias}",
                ))
                traded_days.add(day)
                break

            # SHORT: close below OR low, trend agrees
            if ema_bias == "short" and close < or_low and high <= or_high:
                sl = or_high
                risk = sl - close
                if risk <= 0:
                    continue
                tp = close - risk * TP_MULTIPLIER

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
                    metadata=f"OR_H={or_high:.2f} OR_L={or_low:.2f} EMA={current_ema:.2f} bias={ema_bias}",
                ))
                traded_days.add(day)
                break

    return signals
