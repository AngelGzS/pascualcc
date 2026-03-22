"""Backtest Telegram signals against historical Binance data."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from config import settings
from data.fetcher import FallbackFetcher
from telegram.client import TelegramListener
from telegram.parser import TelegramSignal, parse_signal

logger = logging.getLogger(__name__)

# Fetch enough 1m candles to cover max trade duration (7 days)
MAX_TRADE_DURATION_HOURS = 168  # 7 days
SIMULATION_CANDLES = MAX_TRADE_DURATION_HOURS * 60  # 1m candles


@dataclass
class CopyTradeRecord:
    """Record of a simulated copy trade."""
    pair: str
    direction: str
    leverage: int
    entry_price: float
    entry_time: int
    exit_price: float
    exit_time: int
    exit_reason: str          # "tp1" | "tp2" | "tp3" | "tp4" | "stop_loss" | "timeout"
    targets: list[float] = field(default_factory=list)
    targets_hit: int = 0
    stop_loss: float = 0.0
    margin_used: float = 0.0
    pnl_usd: float = 0.0
    pnl_percent: float = 0.0  # On margin (includes leverage effect)
    duration_hours: float = 0.0


class SignalBacktester:
    """Backtest Telegram signals by replaying them against historical 1m Binance data."""

    def __init__(
        self,
        initial_capital: float = settings.INITIAL_CAPITAL,
        risk_per_trade: float = settings.RISK_PER_TRADE,
        leverage_mult: float = 1.0,  # Leverage multiplier (1.0 = use signal's leverage as-is)
    ) -> None:
        self.initial_capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.leverage_mult = leverage_mult
        self.fetcher = FallbackFetcher()

    async def run(self, channel_name: str, days: int = 90) -> list[CopyTradeRecord]:
        """Fetch signal history from Telegram and backtest each one."""
        # Fetch messages from Telegram
        listener = TelegramListener(channel_name=channel_name, on_message=lambda *a: None)
        messages = await listener.fetch_history(days=days)

        logger.info("Fetched %d messages, parsing signals...", len(messages))

        # Parse signals
        signals: list[TelegramSignal] = []
        for msg in messages:
            sig = parse_signal(msg["text"], timestamp=msg["timestamp_ms"])
            if sig is not None:
                signals.append(sig)

        logger.info("Found %d valid signals out of %d messages", len(signals), len(messages))

        if not signals:
            logger.warning("No signals found in channel history")
            return []

        # Simulate each signal
        trades: list[CopyTradeRecord] = []
        capital = self.initial_capital

        for i, sig in enumerate(signals):
            # Skip if capital is depleted
            if capital <= 0:
                logger.warning("Capital depleted ($%.2f), skipping remaining signals", capital)
                break

            # Cap leverage to sane values
            if sig.leverage > 50:
                logger.warning("Capping leverage from %dx to 20x for %s", sig.leverage, sig.pair)
                sig.leverage = 20

            logger.info(
                "Simulating signal %d/%d: %s %s %dx",
                i + 1, len(signals), sig.direction.upper(), sig.pair, sig.leverage,
            )

            trade = self._simulate_trade(sig, capital)
            if trade is None:
                logger.warning("Could not simulate %s (no data)", sig.pair)
                continue

            trades.append(trade)
            # Cap loss: can't lose more than the margin used
            capped_pnl = max(trade.pnl_usd, -trade.margin_used)
            capital += capped_pnl

            logger.info(
                "  Result: %s PnL=$%.2f (%.1f%%) targets_hit=%d/%d",
                trade.exit_reason, trade.pnl_usd, trade.pnl_percent * 100,
                trade.targets_hit, len(trade.targets),
            )

        # Print report
        self._print_report(trades, signals)
        return trades

    def _simulate_trade(self, signal: TelegramSignal, capital: float) -> CopyTradeRecord | None:
        """Simulate a single trade using 1m Binance candles."""
        if not signal.targets or signal.stop_loss <= 0:
            return None

        # Fetch 1m candles starting from signal timestamp
        try:
            df = self.fetcher.fetch_all_klines(
                symbol=signal.pair,
                interval="1m",
                start_ms=signal.timestamp,
                end_ms=signal.timestamp + (MAX_TRADE_DURATION_HOURS * 3600 * 1000),
            )
        except Exception as e:
            logger.error("Failed to fetch data for %s: %s", signal.pair, e)
            return None

        if df.empty or len(df) < 2:
            return None

        # Entry at first candle close (market order)
        entry_price = float(df["close"].iloc[0])
        entry_time = int(df["timestamp"].iloc[0])

        # Position sizing — apply leverage multiplier (e.g. 80% of signal leverage)
        effective_leverage = max(1, int(signal.leverage * self.leverage_mult))
        margin = min(capital * self.risk_per_trade, capital * 0.05)  # Never risk more than 5%
        notional = margin * effective_leverage

        # Validate signal direction vs targets/SL
        if signal.direction == "long":
            # For longs: targets should be ABOVE entry, SL BELOW
            valid_targets = [t for t in signal.targets if t > entry_price * 0.95]
            if signal.stop_loss > entry_price * 1.5:
                logger.warning("Invalid SL for LONG %s: SL=%.4f > entry=%.4f", signal.pair, signal.stop_loss, entry_price)
                return None
        else:
            # For shorts: targets should be BELOW entry, SL ABOVE
            valid_targets = [t for t in signal.targets if t < entry_price * 1.05]
            if signal.stop_loss < entry_price * 0.5:
                logger.warning("Invalid SL for SHORT %s: SL=%.4f < entry=%.4f", signal.pair, signal.stop_loss, entry_price)
                return None

        if not valid_targets:
            logger.warning("No valid targets for %s %s (entry=%.4f, targets=%s)",
                          signal.direction, signal.pair, entry_price, signal.targets)
            return None

        # Sort targets based on direction
        targets = sorted(valid_targets)
        if signal.direction == "short":
            targets = sorted(valid_targets, reverse=True)  # Descending for shorts

        # Use signal SL as-is (group knows the right distance for their leverage)
        sl = signal.stop_loss

        # State tracking
        targets_hit = 0
        remaining_pct = 1.0  # 100% of position
        total_pnl = 0.0
        tp_size = 0.25  # 25% at each target

        exit_price = entry_price
        exit_time = entry_time
        exit_reason = "timeout"

        # Walk through candles
        for idx in range(1, len(df)):
            candle_high = float(df["high"].iloc[idx])
            candle_low = float(df["low"].iloc[idx])
            candle_ts = int(df["timestamp"].iloc[idx])

            # --- Check stop loss ---
            sl_hit = False
            if signal.direction == "long" and candle_low <= sl:
                sl_hit = True
                exit_price = sl
            elif signal.direction == "short" and candle_high >= sl:
                sl_hit = True
                exit_price = sl

            if sl_hit:
                # Close remaining position at SL
                pnl = self._calc_pnl(
                    signal.direction, entry_price, exit_price,
                    notional * remaining_pct, signal.leverage,
                )
                total_pnl += pnl
                exit_time = candle_ts
                exit_reason = "stop_loss"
                break

            # --- Check targets ---
            while targets_hit < len(targets):
                tp = targets[targets_hit]
                tp_hit = False

                if signal.direction == "long" and candle_high >= tp:
                    tp_hit = True
                elif signal.direction == "short" and candle_low <= tp:
                    tp_hit = True

                if not tp_hit:
                    break

                # Close 25% at this target
                close_pct = min(tp_size, remaining_pct)
                pnl = self._calc_pnl(
                    signal.direction, entry_price, tp,
                    notional * close_pct, signal.leverage,
                )
                total_pnl += pnl
                remaining_pct -= close_pct
                targets_hit += 1
                exit_price = tp
                exit_time = candle_ts

                # After TP1: move SL to breakeven
                if targets_hit == 1:
                    sl = entry_price

            # All targets hit
            if remaining_pct <= 0.001:
                exit_reason = f"tp{targets_hit}"
                break
        else:
            # Timeout — close remaining at last candle
            if remaining_pct > 0.001:
                last_close = float(df["close"].iloc[-1])
                pnl = self._calc_pnl(
                    signal.direction, entry_price, last_close,
                    notional * remaining_pct, signal.leverage,
                )
                total_pnl += pnl
                exit_price = last_close
                exit_time = int(df["timestamp"].iloc[-1])
                exit_reason = f"timeout_tp{targets_hit}" if targets_hit > 0 else "timeout"

        # Calculate duration
        duration_hours = (exit_time - entry_time) / 3_600_000

        # Cap total PnL: max loss = margin (liquidation)
        total_pnl = max(total_pnl, -margin)

        # PnL as % of margin
        pnl_pct = total_pnl / margin if margin > 0 else 0.0

        return CopyTradeRecord(
            pair=signal.pair,
            direction=signal.direction,
            leverage=signal.leverage,
            entry_price=entry_price,
            entry_time=entry_time,
            exit_price=exit_price,
            exit_time=exit_time,
            exit_reason=exit_reason,
            targets=signal.targets,
            targets_hit=targets_hit,
            stop_loss=signal.stop_loss,
            margin_used=margin,
            pnl_usd=total_pnl,
            pnl_percent=pnl_pct,
            duration_hours=duration_hours,
        )

    @staticmethod
    def _calc_pnl(
        direction: str, entry: float, exit_price: float,
        notional: float, leverage: int,
    ) -> float:
        """Calculate PnL for a futures position chunk."""
        if direction == "long":
            price_change_pct = (exit_price - entry) / entry
        else:
            price_change_pct = (entry - exit_price) / entry
        return notional * price_change_pct

    def _print_report(self, trades: list[CopyTradeRecord], signals: list[TelegramSignal]) -> None:
        """Print backtest report to console."""
        if not trades:
            print("No trades to report.")
            return

        total_pnl = sum(t.pnl_usd for t in trades)
        total_pnl_pct = total_pnl / self.initial_capital * 100

        wins = [t for t in trades if t.pnl_usd > 0]
        losses = [t for t in trades if t.pnl_usd <= 0]
        wr = len(wins) / len(trades) * 100 if trades else 0

        gross_profit = sum(t.pnl_usd for t in wins)
        gross_loss = abs(sum(t.pnl_usd for t in losses))
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Max drawdown
        peak = self.initial_capital
        equity = self.initial_capital
        max_dd = 0.0
        for t in trades:
            equity += t.pnl_usd
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        # Average R:R
        avg_win = sum(t.pnl_usd for t in wins) / len(wins) if wins else 0
        avg_loss = abs(sum(t.pnl_usd for t in losses) / len(losses)) if losses else 1
        avg_rr = avg_win / avg_loss if avg_loss > 0 else 0

        # Target distribution
        tp_dist = [0, 0, 0, 0, 0]  # 0=none, 1=tp1, 2=tp2, 3=tp3, 4=tp4
        for t in trades:
            tp_dist[min(t.targets_hit, 4)] += 1

        # Best and worst
        best = max(trades, key=lambda t: t.pnl_usd)
        worst = min(trades, key=lambda t: t.pnl_usd)

        avg_duration = sum(t.duration_hours for t in trades) / len(trades)

        print()
        print("=" * 65)
        print(f"  SIGNAL BACKTEST REPORT  |  {len(signals)} signals parsed")
        print("=" * 65)
        print(f"  Traded:         {len(trades)} ({len(signals) - len(trades)} skipped)")
        print(f"  Win rate:       {wr:.0f}%")
        print(f"  Profit factor:  {pf:.2f}")
        print(f"  Avg R:R:        {avg_rr:.1f}:1")
        print(f"  Total PnL:      ${total_pnl:+,.2f} ({total_pnl_pct:+.1f}%)")
        print(f"  Max drawdown:   {max_dd:.1%}")
        print(f"  Avg duration:   {avg_duration:.1f}h")
        print(f"{'-' * 65}")
        print(f"  Targets hit distribution:")
        print(f"    0 (SL/timeout): {tp_dist[0]:3d}  |  TP1: {tp_dist[1]:3d}  |  TP2: {tp_dist[2]:3d}")
        print(f"    TP3:            {tp_dist[3]:3d}  |  TP4 (full): {tp_dist[4]:3d}")
        print(f"{'-' * 65}")
        print(f"  Best:   {best.direction.upper()} {best.pair} ${best.pnl_usd:+,.2f} ({best.exit_reason})")
        print(f"  Worst:  {worst.direction.upper()} {worst.pair} ${worst.pnl_usd:+,.2f} ({worst.exit_reason})")
        print(f"{'-' * 65}")

        # Per-trade detail
        print("  Trade log:")
        equity = self.initial_capital
        for t in trades:
            equity += t.pnl_usd
            ts = datetime.fromtimestamp(t.entry_time / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")
            marker = "W" if t.pnl_usd > 0 else "L"
            print(
                f"    [{marker}] {ts} {t.direction.upper():5s} {t.pair:12s} "
                f"${t.pnl_usd:+8.2f} ({t.pnl_percent:+6.1%}) "
                f"TP{t.targets_hit}/{len(t.targets)} "
                f"equity=${equity:,.2f}"
            )

        print("=" * 65)
