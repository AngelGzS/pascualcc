"""PO3-specific backtest engine: simulates trades on 15m data."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from strategies.po3 import settings as po3_settings
from strategies.po3.types import PO3Signal, PO3TradeRecord, FVG, LiquiditySweep, CISD
from strategies.po3.session import (
    resample_to_4h_est,
    get_candle_est_hour,
    is_weekday,
    get_trading_day,
)
from strategies.po3.detector_fvg import detect_fvgs, find_nearest_fvg, update_fvg_fills

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Lightweight sweep / CISD / signal detection (inline, no PO3Engine needed)
# ---------------------------------------------------------------------------

def _detect_pivots(
    highs: np.ndarray,
    lows: np.ndarray,
    left: int,
    right: int,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Return lists of (bar_index, price) for pivot highs and pivot lows."""
    pivot_highs: list[tuple[int, float]] = []
    pivot_lows: list[tuple[int, float]] = []
    for i in range(left, len(highs) - right):
        if all(highs[i] >= highs[i - j] for j in range(1, left + 1)) and all(
            highs[i] >= highs[i + j] for j in range(1, right + 1)
        ):
            pivot_highs.append((i, float(highs[i])))
        if all(lows[i] <= lows[i - j] for j in range(1, left + 1)) and all(
            lows[i] <= lows[i + j] for j in range(1, right + 1)
        ):
            pivot_lows.append((i, float(lows[i])))
    return pivot_highs, pivot_lows


def _detect_sweeps(
    df: pd.DataFrame,
    pivot_highs: list[tuple[int, float]],
    pivot_lows: list[tuple[int, float]],
) -> list[LiquiditySweep]:
    """Detect candles that swept a prior swing high/low then reversed."""
    sweeps: list[LiquiditySweep] = []
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    timestamps = df["timestamp"].values

    for idx in range(1, len(df)):
        # High sweep: candle high exceeds a pivot high but closes below it
        for _, ph_price in pivot_highs:
            if highs[idx] > ph_price and closes[idx] < ph_price:
                sweeps.append(
                    LiquiditySweep(
                        timestamp=int(timestamps[idx]),
                        sweep_type="high_sweep",
                        level_swept=ph_price,
                        sweep_candle_high=float(highs[idx]),
                        sweep_candle_low=float(lows[idx]),
                        bar_index=idx,
                    )
                )
                break

        # Low sweep: candle low goes below a pivot low but closes above it
        for _, pl_price in pivot_lows:
            if lows[idx] < pl_price and closes[idx] > pl_price:
                sweeps.append(
                    LiquiditySweep(
                        timestamp=int(timestamps[idx]),
                        sweep_type="low_sweep",
                        level_swept=pl_price,
                        sweep_candle_high=float(highs[idx]),
                        sweep_candle_low=float(lows[idx]),
                        bar_index=idx,
                    )
                )
                break

    return sweeps


def _avg_body(df: pd.DataFrame, end_idx: int, lookback: int) -> float:
    start = max(0, end_idx - lookback)
    bodies = (df["close"].iloc[start:end_idx] - df["open"].iloc[start:end_idx]).abs()
    return float(bodies.mean()) if len(bodies) > 0 else 0.0


def _get_6am_bias(
    df_4h: pd.DataFrame,
    trading_day: str,
) -> str | None:
    """Determine directional bias from the 6AM EST 4H candle.

    Rules:
    - 6AM candle sweeps HIGH of previous 4H candle → SHORT bias
    - 6AM candle sweeps LOW of previous 4H candle → LONG bias
    - If both or neither → no bias (None)
    """
    from strategies.po3.session import get_candle_est_hour, get_trading_day

    for i in range(1, len(df_4h)):
        ts = int(df_4h["timestamp"].iloc[i])
        hour = get_candle_est_hour(ts)
        day = str(get_trading_day(ts))

        # Allow ±1 hour to handle EST/EDT differences
        bias_hour = po3_settings.BIAS_CANDLE_HOUR
        if day != trading_day or hour not in (bias_hour, bias_hour + 1, bias_hour - 1):
            continue

        # 6AM candle and previous candle
        prev_high = float(df_4h["high"].iloc[i - 1])
        prev_low = float(df_4h["low"].iloc[i - 1])
        candle_high = float(df_4h["high"].iloc[i])
        candle_low = float(df_4h["low"].iloc[i])
        candle_close = float(df_4h["close"].iloc[i])

        swept_high = candle_high > prev_high and candle_close < prev_high
        swept_low = candle_low < prev_low and candle_close > prev_low

        if swept_high and not swept_low:
            logger.debug("  6AM bias for %s: SHORT (swept prev high %.2f)", day, prev_high)
            return "short"
        if swept_low and not swept_high:
            logger.debug("  6AM bias for %s: LONG (swept prev low %.2f)", day, prev_low)
            return "long"

    return None


def _generate_signals(
    df_15m: pd.DataFrame,
    df_4h: pd.DataFrame,
    symbol: str,
    rr_target: float,
) -> list[PO3Signal]:
    """Walk through 15m data and generate PO3 signals.

    Full PO3 pipeline:
    1. For each trading day, check 6AM 4H candle for directional bias
    2. If bias exists, look for sweeps in the 10AM-2PM entry window on 15m
    3. After a sweep agreeing with bias, look for displacement + FVG = CISD
    4. Validate sweep tapped into a HTF FVG
    5. Build signal with entry at FVG midpoint, SL at FVG edge
    """
    signals: list[PO3Signal] = []

    highs = df_15m["high"].values
    lows = df_15m["low"].values
    closes = df_15m["close"].values
    opens = df_15m["open"].values
    timestamps = df_15m["timestamp"].values

    # Pre-compute 4H FVGs
    htf_fvgs = detect_fvgs(df_4h, "4h")
    logger.debug("Detected %d HTF (4H) FVGs", len(htf_fvgs))

    # Pre-compute pivots on 15m
    pivot_highs, pivot_lows = _detect_pivots(
        highs, lows, po3_settings.PIVOT_LEFT, po3_settings.PIVOT_RIGHT
    )
    logger.debug("Detected %d pivot highs, %d pivot lows", len(pivot_highs), len(pivot_lows))

    # Pre-compute sweeps
    sweeps = _detect_sweeps(df_15m, pivot_highs, pivot_lows)
    logger.debug("Detected %d raw sweeps", len(sweeps))

    # Pre-compute 15m FVGs
    fvgs_15m = detect_fvgs(df_15m, "15m")
    logger.debug("Detected %d 15m FVGs", len(fvgs_15m))

    # Get unique trading days
    from strategies.po3.session import get_trading_day
    all_days = set()
    for ts in timestamps:
        all_days.add(str(get_trading_day(int(ts))))

    # Pre-compute 6AM bias for each day
    daily_bias: dict[str, str] = {}
    for day in sorted(all_days):
        bias = _get_6am_bias(df_4h, day)
        if bias:
            daily_bias[day] = bias
    logger.debug("Days with 6AM bias: %d / %d", len(daily_bias), len(all_days))

    trades_today: dict[str, int] = {}
    used_cisd_bars: set[int] = set()  # prevent duplicate signals

    for sweep in sweeps:
        ts = sweep.timestamp
        idx = sweep.bar_index

        # --- Filter: weekday only ---
        if po3_settings.TRADE_WEEKDAYS_ONLY and not is_weekday(ts):
            continue

        # --- Filter: must be in 10AM-2PM entry window (±1h for EST/EDT) ---
        est_hour = get_candle_est_hour(ts)
        window_start = po3_settings.ENTRY_WINDOW_START
        window_end = po3_settings.ENTRY_WINDOW_END
        if not (window_start <= est_hour < window_end or (window_start + 1) <= est_hour < (window_end + 1)):
            continue

        # --- Filter: max daily trades ---
        day_key = str(get_trading_day(ts))
        if trades_today.get(day_key, 0) >= po3_settings.MAX_DAILY_TRADES:
            continue

        # --- Filter: 6AM bias must exist and agree with sweep ---
        bias = daily_bias.get(day_key)
        if bias is None:
            continue

        # Determine direction from sweep
        if sweep.sweep_type == "high_sweep":
            direction = "short"
            bias_type = "high_sweep_short"
        else:
            direction = "long"
            bias_type = "low_sweep_long"

        # Sweep direction must agree with 6AM bias
        if direction != bias:
            continue

        # --- Validate sweep tapped into a HTF FVG ---
        # For SHORT (high_sweep): the sweep high should be inside a bearish HTF FVG
        # For LONG (low_sweep): the sweep low should be inside a bullish HTF FVG
        htf_fvg = None
        sweep_price = sweep.sweep_candle_high if direction == "short" else sweep.sweep_candle_low
        for fvg in htf_fvgs:
            if fvg.filled:
                continue
            if direction == "short" and fvg.direction == "bearish":
                if fvg.bottom <= sweep_price <= fvg.top:
                    htf_fvg = fvg
                    break
            elif direction == "long" and fvg.direction == "bullish":
                if fvg.bottom <= sweep_price <= fvg.top:
                    htf_fvg = fvg
                    break

        if htf_fvg is None:
            continue

        # --- Look for CISD: displacement + FVG after sweep ---
        cisd = None
        entry_fvg = None
        avg_body_size = _avg_body(df_15m, idx, po3_settings.DISPLACEMENT_LOOKBACK)
        threshold_body = avg_body_size * po3_settings.DISPLACEMENT_BODY_MULT

        for j in range(idx + 1, min(idx + 6, len(df_15m))):
            # Skip if we already used this bar for a signal
            if j in used_cisd_bars:
                continue

            body = abs(closes[j] - opens[j])
            if body < threshold_body:
                continue

            # Displacement must agree with direction
            if direction == "short" and closes[j] >= opens[j]:
                continue
            if direction == "long" and closes[j] <= opens[j]:
                continue

            # Find a 15m FVG formed around this displacement
            for fvg in fvgs_15m:
                if fvg.filled:
                    continue
                if abs(fvg.bar_index - j) > 2:
                    continue
                if direction == "long" and fvg.direction == "bullish":
                    entry_fvg = fvg
                    break
                if direction == "short" and fvg.direction == "bearish":
                    entry_fvg = fvg
                    break

            if entry_fvg is not None:
                cisd = CISD(
                    timestamp=int(timestamps[j]),
                    direction="bullish" if direction == "long" else "bearish",
                    displacement_index=j,
                    fvg=entry_fvg,
                    bar_index=j,
                )
                break

        if cisd is None or entry_fvg is None:
            continue

        # --- Build trade levels ---
        entry_price = entry_fvg.midpoint
        if direction == "long":
            stop_loss = entry_fvg.bottom
        else:
            stop_loss = entry_fvg.top
        risk_points = abs(entry_price - stop_loss)
        if risk_points <= 0:
            continue

        # Sanity: risk_points should be reasonable (0.1% - 2% of price)
        risk_pct = risk_points / entry_price
        if risk_pct > 0.02:
            logger.debug("  Skipping signal: risk %.2f%% too large", risk_pct * 100)
            continue
        if risk_pct < 0.001:
            logger.debug("  Skipping signal: risk %.4f%% too small (FVG too tight)", risk_pct * 100)
            continue

        tp_2r = entry_price + (risk_points * 2 * (1 if direction == "long" else -1))
        tp_3r = entry_price + (risk_points * 3 * (1 if direction == "long" else -1))

        signal = PO3Signal(
            timestamp=int(timestamps[cisd.bar_index]),
            symbol=symbol,
            direction=direction,
            bias_type=bias_type,
            htf_fvg=htf_fvg,
            entry_fvg=entry_fvg,
            sweep=sweep,
            cisd=cisd,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_2r=tp_2r,
            take_profit_3r=tp_3r,
            risk_points=risk_points,
        )
        signals.append(signal)
        trades_today[day_key] = trades_today.get(day_key, 0) + 1
        used_cisd_bars.add(cisd.bar_index)

        logger.info(
            "  Signal: %s %s @ %.2f | SL=%.2f | TP2R=%.2f | risk=%.2f pts | day=%s",
            direction.upper(), symbol, entry_price, stop_loss, tp_2r, risk_points, day_key,
        )

    return signals


# ---------------------------------------------------------------------------
#  Backtest engine
# ---------------------------------------------------------------------------

class PO3BacktestEngine:
    """Backtest engine for the PO3 strategy.

    Takes 15m OHLCV data, runs signal detection to find setups,
    then simulates each trade forward through the 15m candles.
    """

    def __init__(
        self,
        initial_capital: float = po3_settings.INITIAL_CAPITAL,
        risk_per_trade: float = po3_settings.RISK_PER_TRADE,
        rr_target: float = po3_settings.DEFAULT_RR,
        commission: float = po3_settings.COMMISSION_RATE,
        slippage: float = po3_settings.SLIPPAGE_RATE,
    ) -> None:
        self.initial_capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.rr_target = rr_target
        self.commission = commission
        self.slippage = slippage

    # ── public API ────────────────────────────────────────────────────────

    def run(self, df_15m: pd.DataFrame, symbol: str) -> list[PO3TradeRecord]:
        """Run backtest on 15m data.

        1. Resample to 4H for HTF context
        2. Generate all PO3 signals
        3. Simulate each trade forward
        4. Return list of trade records
        """
        if df_15m.empty or len(df_15m) < 50:
            logger.warning("Insufficient data for PO3 backtest (%d candles)", len(df_15m))
            return []

        df_4h = resample_to_4h_est(df_15m)
        logger.info(
            "PO3 backtest: %d 15m candles, %d 4H candles",
            len(df_15m),
            len(df_4h),
        )

        signals = _generate_signals(df_15m, df_4h, symbol, self.rr_target)
        logger.info("Generated %d PO3 signals", len(signals))

        trades: list[PO3TradeRecord] = []
        capital = self.initial_capital

        for sig in signals:
            record = self._simulate_trade(sig, df_15m, sig.cisd.bar_index, capital)
            if record is not None:
                capital += record.pnl_usd
                trades.append(record)

        return trades

    # ── trade simulation ──────────────────────────────────────────────────

    def _simulate_trade(
        self,
        signal: PO3Signal,
        df: pd.DataFrame,
        signal_bar_idx: int,
        capital: float,
    ) -> PO3TradeRecord | None:
        """Simulate a single trade from signal to exit.

        1. Look for fill within ENTRY_TIMEOUT_CANDLES.
        2. Once filled, walk forward checking SL then TP each candle.
        3. Calculate PnL with commission and slippage.
        """
        highs = df["high"].values
        lows = df["low"].values
        timestamps = df["timestamp"].values
        n = len(df)

        entry_price = signal.entry_price
        direction = signal.direction
        timeout = po3_settings.ENTRY_TIMEOUT_CANDLES

        # Use the RR target from constructor
        if self.rr_target <= 2.0:
            tp_price = signal.take_profit_2r
        else:
            tp_price = signal.take_profit_3r
        sl_price = signal.stop_loss

        # ── Step 1: entry ──────────────────────────────────────────────
        # Try limit order at FVG midpoint first; if no fill within timeout,
        # use market entry at displacement candle close
        fill_bar = None
        fill_price = entry_price

        for i in range(signal_bar_idx + 1, min(signal_bar_idx + 1 + timeout, n)):
            if direction == "long" and lows[i] <= entry_price:
                fill_bar = i
                fill_price = entry_price * (1 + self.slippage)
                break
            if direction == "short" and highs[i] >= entry_price:
                fill_bar = i
                fill_price = entry_price * (1 - self.slippage)
                break

        if fill_bar is None:
            # Fallback: market entry at displacement candle close
            if signal_bar_idx < n:
                fill_bar = signal_bar_idx
                fill_price = float(df["close"].iloc[signal_bar_idx])
                if direction == "long":
                    fill_price *= (1 + self.slippage)
                else:
                    fill_price *= (1 - self.slippage)
            else:
                return None

        # ── Step 2: walk forward for SL / TP ─────────────────────────────
        exit_bar = None
        exit_price = 0.0
        exit_reason = ""

        for i in range(fill_bar + 1, n):
            # Check SL first (protect downside)
            if direction == "long" and lows[i] <= sl_price:
                exit_bar = i
                exit_price = sl_price * (1 - self.slippage)
                exit_reason = "stop_loss"
                break
            if direction == "short" and highs[i] >= sl_price:
                exit_bar = i
                exit_price = sl_price * (1 + self.slippage)
                exit_reason = "stop_loss"
                break

            # Check TP
            if direction == "long" and highs[i] >= tp_price:
                exit_bar = i
                exit_price = tp_price
                exit_reason = f"tp{int(self.rr_target)}r"
                break
            if direction == "short" and lows[i] <= tp_price:
                exit_bar = i
                exit_price = tp_price
                exit_reason = f"tp{int(self.rr_target)}r"
                break

        # If neither SL nor TP hit, close at last candle
        if exit_bar is None:
            exit_bar = n - 1
            exit_price = float(df["close"].iloc[-1])
            exit_reason = "timeout"

        # ── Step 3: PnL calculation ──────────────────────────────────────
        # risk_amount = the max $ we want to lose on SL hit = 1R
        risk_amount = capital * self.risk_per_trade
        # risk_fraction = how far SL is from entry as fraction of entry price
        risk_fraction = abs(fill_price - sl_price) / fill_price if fill_price > 0 else 1.0
        # position_size = sized so that SL hit = lose exactly risk_amount
        position_size_usd = risk_amount / risk_fraction if risk_fraction > 0 else 0.0

        if direction == "long":
            price_change_pct = (exit_price - fill_price) / fill_price
        else:
            price_change_pct = (fill_price - exit_price) / fill_price

        gross_pnl = position_size_usd * price_change_pct
        commission_cost = position_size_usd * self.commission * 2  # entry + exit
        net_pnl = gross_pnl - commission_cost

        # Cap losses at -1R to prevent >1R losses from slippage/gaps
        if net_pnl < -risk_amount:
            net_pnl = -risk_amount

        pnl_pct = net_pnl / capital if capital > 0 else 0.0

        duration_minutes = 0
        if timestamps[exit_bar] > timestamps[fill_bar]:
            duration_minutes = int((timestamps[exit_bar] - timestamps[fill_bar]) / 60_000)

        return PO3TradeRecord(
            signal=signal,
            entry_fill_price=fill_price,
            exit_price=exit_price,
            exit_reason=exit_reason,
            pnl_usd=net_pnl,
            pnl_percent=pnl_pct,
            duration_minutes=duration_minutes,
            entry_time=int(timestamps[fill_bar]),
            exit_time=int(timestamps[exit_bar]),
        )

    # ── report ────────────────────────────────────────────────────────────

    def print_report(self, trades: list[PO3TradeRecord], symbol: str, days: int = 0) -> None:
        """Print formatted backtest report."""
        # Count signals (trades + no-fills would need the full signal list,
        # but we only have trades here; the run() logs total signals)
        total_trades = len(trades)
        if total_trades == 0:
            print(f"\n  No trades generated for {symbol}.\n")
            return

        wins = [t for t in trades if t.pnl_usd > 0]
        losses = [t for t in trades if t.pnl_usd <= 0]
        win_rate = len(wins) / total_trades * 100

        gross_profit = sum(t.pnl_usd for t in wins)
        gross_loss = abs(sum(t.pnl_usd for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        total_pnl = sum(t.pnl_usd for t in trades)
        total_pnl_pct = sum(t.pnl_percent for t in trades) * 100

        # Average R:R for winners
        avg_rr = 0.0
        if wins:
            rrs = []
            for t in wins:
                risk = abs(t.entry_fill_price - t.signal.stop_loss)
                reward = abs(t.exit_price - t.entry_fill_price)
                rrs.append(reward / risk if risk > 0 else 0.0)
            avg_rr = sum(rrs) / len(rrs)

        # Max drawdown
        equity_curve = []
        running = self.initial_capital
        for t in trades:
            running += t.pnl_usd
            equity_curve.append(running)
        peak = self.initial_capital
        max_dd = 0.0
        for eq in equity_curve:
            peak = max(peak, eq)
            dd = (peak - eq) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)

        days_label = f"  |  {days} days" if days > 0 else ""

        print()
        print("=" * 65)
        print(f"  PO3 BACKTEST REPORT  |  {symbol}{days_label}")
        print("=" * 65)
        print(f"  Traded:         {total_trades}")
        print(f"  Win rate:       {win_rate:.0f}%")
        print(f"  Profit factor:  {profit_factor:.2f}")
        print(f"  Avg R:R:        {avg_rr:.1f}:1")
        sign = "+" if total_pnl >= 0 else ""
        print(f"  Total PnL:      ${sign}{total_pnl:.2f} ({sign}{total_pnl_pct:.1f}%)")
        print(f"  Max drawdown:   {max_dd * 100:.1f}%")
        print("-" * 65)
        print("  Trade log:")

        equity = self.initial_capital
        for t in trades:
            equity += t.pnl_usd
            tag = "[W]" if t.pnl_usd > 0 else "[L]"
            dt = datetime.fromtimestamp(t.entry_time / 1000, tz=timezone.utc)
            date_str = dt.strftime("%m-%d %H:%M")
            direction_str = t.signal.direction.upper()

            risk = abs(t.entry_fill_price - t.signal.stop_loss)
            r_mult = (t.pnl_usd / (self.initial_capital * self.risk_per_trade)) if risk > 0 else 0.0
            r_sign = "+" if r_mult >= 0 else ""
            pnl_sign = "+" if t.pnl_usd >= 0 else ""

            print(
                f"    {tag} {date_str} {direction_str:<5} {symbol}  "
                f"${pnl_sign}{t.pnl_usd:.2f} ({r_sign}{r_mult:.1f}R) "
                f"equity=${equity:,.2f}"
            )

        print("=" * 65)
        print()
