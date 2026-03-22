"""Strategy 7: VWAP Standard Deviation Mean Reversion.

Rules (from research):
- Compute daily VWAP with 1, 2, 3 sigma deviation bands
- When VWAP is flat (not trending), enter mean reversion at 2 sigma
  - Price at -2 sigma: LONG targeting VWAP
  - Price at +2 sigma: SHORT targeting VWAP
- SL: beyond 3 sigma band
- TP: VWAP (the mean)
- Filter: skip if VWAP slope is too steep (trending day)
- Session: 10:00-14:00 EST (after VWAP stabilizes, before close)
- Max 2 trades per day
- Expected: ~65-75% WR, 1:1 R:R
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.multi_strategy import SimpleSignal
from strategies.intraday.session_utils import add_est_columns, get_session_candles

STRATEGY_NAME = "VWAP 2-Sigma Reversion"

ENTRY_START_HOUR = 10
ENTRY_END_HOUR = 14
MAX_DAILY = 2
VWAP_SLOPE_MAX = 0.0003  # max VWAP slope as fraction of price (0.03%)


def _compute_daily_vwap(df: pd.DataFrame, day: str) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series] | None:
    """Compute VWAP and std dev bands for a single day.

    Returns: (vwap, upper_2sd, lower_2sd, upper_3sd, lower_3sd) as Series
    aligned to the day's candle indices.
    """
    day_mask = df["tday"] == day
    day_df = df[day_mask]
    if len(day_df) < 10:
        return None

    typical_price = (day_df["high"] + day_df["low"] + day_df["close"]) / 3
    volume = day_df["volume"].replace(0, 1)  # avoid div by zero

    cum_tp_vol = (typical_price * volume).cumsum()
    cum_vol = volume.cumsum()
    vwap = cum_tp_vol / cum_vol

    # Compute running standard deviation of price from VWAP
    # Variance = cumsum((TP - VWAP)^2 * vol) / cumsum(vol)
    sq_diff = ((typical_price - vwap) ** 2 * volume).cumsum()
    variance = sq_diff / cum_vol
    std_dev = np.sqrt(variance)

    upper_2sd = vwap + 2 * std_dev
    lower_2sd = vwap - 2 * std_dev
    upper_3sd = vwap + 3 * std_dev
    lower_3sd = vwap - 3 * std_dev

    return vwap, upper_2sd, lower_2sd, upper_3sd, lower_3sd


def generate_signals(df: pd.DataFrame, symbol: str) -> list[SimpleSignal]:
    df = add_est_columns(df)
    signals: list[SimpleSignal] = []
    trades_today: dict[str, int] = {}

    all_days = sorted(df["tday"].unique())

    for day in all_days:
        if trades_today.get(day, 0) >= MAX_DAILY:
            continue

        result = _compute_daily_vwap(df, day)
        if result is None:
            continue

        vwap, upper_2sd, lower_2sd, upper_3sd, lower_3sd = result

        day_mask = df["tday"] == day
        day_df = df[day_mask]

        # Check VWAP slope (trending filter)
        # Use last 8 candles of VWAP to determine slope
        if len(vwap) >= 8:
            vwap_vals = vwap.values
            recent = vwap_vals[-8:]
            slope = abs(recent[-1] - recent[0]) / recent[0] if recent[0] > 0 else 0
            if slope > VWAP_SLOPE_MAX * 8:  # scale by number of candles
                continue  # trending day, skip

        for i, (idx, row) in enumerate(day_df.iterrows()):
            if trades_today.get(day, 0) >= MAX_DAILY:
                break

            idx_int = int(idx)
            est_h = int(row["est_hour"])
            if est_h < ENTRY_START_HOUR or est_h >= ENTRY_END_HOUR:
                continue
            if int(row["weekday"]) >= 5:
                continue

            close = float(row["close"])
            low = float(row["low"])
            high = float(row["high"])

            # Get VWAP values at this position
            pos = day_df.index.get_loc(idx)
            v = float(vwap.iloc[pos])
            u2 = float(upper_2sd.iloc[pos])
            l2 = float(lower_2sd.iloc[pos])
            u3 = float(upper_3sd.iloc[pos])
            l3 = float(lower_3sd.iloc[pos])

            # Bands must be meaningful (at least 0.05% wide)
            band_width = u2 - l2
            if band_width / v < 0.0005:
                continue

            # LONG: price touches or closes below lower 2 sigma
            if close <= l2 and close > l3:
                sl = l3
                tp = v  # target VWAP
                risk = close - sl
                if risk <= 0:
                    continue
                # Only take if R:R is reasonable (TP should be > SL distance)
                reward = tp - close
                if reward <= 0 or reward < risk * 0.5:
                    continue

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
                    metadata=f"VWAP={v:.2f} L2={l2:.2f} L3={l3:.2f}",
                ))
                trades_today[day] = trades_today.get(day, 0) + 1

            # SHORT: price touches or closes above upper 2 sigma
            elif close >= u2 and close < u3:
                sl = u3
                tp = v  # target VWAP
                risk = sl - close
                if risk <= 0:
                    continue
                reward = close - tp
                if reward <= 0 or reward < risk * 0.5:
                    continue

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
                    metadata=f"VWAP={v:.2f} U2={u2:.2f} U3={u3:.2f}",
                ))
                trades_today[day] = trades_today.get(day, 0) + 1

    return signals
