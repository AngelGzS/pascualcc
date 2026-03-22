"""Multi-strategy backtest framework.

Each strategy implements generate_signals() returning a list of SimpleSignal.
The framework handles simulation, PnL, and reporting uniformly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class SimpleSignal:
    """Universal signal format for all strategies."""
    timestamp: int          # Unix ms when signal was generated
    bar_index: int          # index in the 15m DataFrame
    symbol: str
    direction: str          # 'long' | 'short'
    entry_price: float      # limit or market price
    stop_loss: float
    take_profit: float
    strategy_name: str
    entry_type: str = "market"  # 'market' | 'limit'
    metadata: str = ""      # extra info for debugging


@dataclass
class TradeResult:
    signal: SimpleSignal
    fill_price: float
    exit_price: float
    exit_reason: str        # 'tp', 'sl', 'timeout'
    pnl_usd: float
    r_multiple: float       # PnL in R terms
    entry_time: int
    exit_time: int


class MultiStrategyBacktester:
    """Backtest any strategy that produces SimpleSignal list."""

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        risk_per_trade: float = 0.02,
        commission: float = 0.001,
        slippage: float = 0.0005,
        entry_timeout: int = 8,
        fixed_risk: bool = True,
    ) -> None:
        self.initial_capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.commission = commission
        self.slippage = slippage
        self.entry_timeout = entry_timeout
        self.fixed_risk = fixed_risk  # True = always risk % of initial capital

    def run(
        self, df: pd.DataFrame, signals: list[SimpleSignal]
    ) -> list[TradeResult]:
        """Simulate all signals on the given OHLCV DataFrame."""
        trades: list[TradeResult] = []
        capital = self.initial_capital
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        timestamps = df["timestamp"].values
        n = len(df)

        for sig in signals:
            idx = sig.bar_index
            if idx >= n - 2:
                continue

            # --- Entry ---
            fill_bar = None
            fill_price = sig.entry_price

            if sig.entry_type == "market":
                fill_bar = idx + 1
                fill_price = float(closes[idx])
                if sig.direction == "long":
                    fill_price *= (1 + self.slippage)
                else:
                    fill_price *= (1 - self.slippage)
            else:
                # Limit order
                for i in range(idx + 1, min(idx + 1 + self.entry_timeout, n)):
                    if sig.direction == "long" and lows[i] <= sig.entry_price:
                        fill_bar = i
                        fill_price = sig.entry_price * (1 + self.slippage)
                        break
                    if sig.direction == "short" and highs[i] >= sig.entry_price:
                        fill_bar = i
                        fill_price = sig.entry_price * (1 - self.slippage)
                        break

            if fill_bar is None:
                continue

            # --- Recalculate SL/TP from fill price if needed ---
            sl_price = sig.stop_loss
            tp_price = sig.take_profit

            # --- Walk forward for exit ---
            exit_bar = None
            exit_price = 0.0
            exit_reason = ""

            for i in range(fill_bar + 1, n):
                # SL first
                if sig.direction == "long" and lows[i] <= sl_price:
                    exit_bar = i
                    exit_price = sl_price * (1 - self.slippage)
                    exit_reason = "sl"
                    break
                if sig.direction == "short" and highs[i] >= sl_price:
                    exit_bar = i
                    exit_price = sl_price * (1 + self.slippage)
                    exit_reason = "sl"
                    break

                # TP
                if sig.direction == "long" and highs[i] >= tp_price:
                    exit_bar = i
                    exit_price = tp_price
                    exit_reason = "tp"
                    break
                if sig.direction == "short" and lows[i] <= tp_price:
                    exit_bar = i
                    exit_price = tp_price
                    exit_reason = "tp"
                    break

            if exit_bar is None:
                exit_bar = n - 1
                exit_price = float(closes[-1])
                exit_reason = "timeout"

            # --- PnL ---
            base_capital = self.initial_capital if self.fixed_risk else capital
            risk_amount = base_capital * self.risk_per_trade
            risk_dist = abs(fill_price - sl_price)
            if risk_dist <= 0:
                continue
            risk_fraction = risk_dist / fill_price
            position_size = risk_amount / risk_fraction

            if sig.direction == "long":
                pct = (exit_price - fill_price) / fill_price
            else:
                pct = (fill_price - exit_price) / fill_price

            gross = position_size * pct
            comm = position_size * self.commission * 2
            net = gross - comm
            r_mult = net / risk_amount if risk_amount > 0 else 0.0

            # Cap at -1R
            if net < -risk_amount:
                net = -risk_amount
                r_mult = -1.0

            capital += net

            trades.append(TradeResult(
                signal=sig,
                fill_price=fill_price,
                exit_price=exit_price,
                exit_reason=exit_reason,
                pnl_usd=net,
                r_multiple=r_mult,
                entry_time=int(timestamps[fill_bar]),
                exit_time=int(timestamps[exit_bar]),
            ))

        return trades

    def print_report(
        self, trades: list[TradeResult], strategy_name: str, symbol: str, days: int
    ) -> dict:
        """Print report and return summary dict."""
        total = len(trades)
        if total == 0:
            print(f"\n  {strategy_name}: No trades on {symbol} ({days}d)\n")
            return {"strategy": strategy_name, "trades": 0}

        wins = [t for t in trades if t.pnl_usd > 0]
        losses = [t for t in trades if t.pnl_usd <= 0]
        wr = len(wins) / total * 100

        gross_p = sum(t.pnl_usd for t in wins)
        gross_l = abs(sum(t.pnl_usd for t in losses))
        pf = gross_p / gross_l if gross_l > 0 else float("inf")

        total_pnl = sum(t.pnl_usd for t in trades)
        avg_r = sum(t.r_multiple for t in trades) / total

        # Max drawdown
        eq = self.initial_capital
        peak = eq
        mdd = 0.0
        for t in trades:
            eq += t.pnl_usd
            peak = max(peak, eq)
            dd = (peak - eq) / peak if peak > 0 else 0.0
            mdd = max(mdd, dd)

        avg_rr_w = 0.0
        if wins:
            avg_rr_w = sum(t.r_multiple for t in wins) / len(wins)

        sign = "+" if total_pnl >= 0 else ""
        pnl_pct = total_pnl / self.initial_capital * 100

        print()
        print("=" * 65)
        print(f"  {strategy_name}  |  {symbol}  |  {days}d")
        print("=" * 65)
        print(f"  Trades:         {total}")
        print(f"  Win rate:       {wr:.0f}%")
        print(f"  Profit factor:  {pf:.2f}")
        print(f"  Avg R:           {avg_r:+.2f}R")
        print(f"  Avg win:        {avg_rr_w:+.2f}R")
        print(f"  Total PnL:      ${sign}{total_pnl:.2f} ({sign}{pnl_pct:.1f}%)")
        print(f"  Max drawdown:   {mdd * 100:.1f}%")
        print("-" * 65)
        print("  Trade log:")

        equity = self.initial_capital
        for t in trades:
            equity += t.pnl_usd
            tag = "[W]" if t.pnl_usd > 0 else "[L]"
            dt = datetime.fromtimestamp(t.entry_time / 1000, tz=timezone.utc)
            date_str = dt.strftime("%m-%d %H:%M")
            d = t.signal.direction.upper()
            r_sign = "+" if t.r_multiple >= 0 else ""
            pnl_sign = "+" if t.pnl_usd >= 0 else ""
            print(
                f"    {tag} {date_str} {d:<5} "
                f"${pnl_sign}{t.pnl_usd:.2f} ({r_sign}{t.r_multiple:.1f}R) "
                f"[{t.exit_reason}] eq=${equity:,.2f}"
            )
        print("=" * 65)

        return {
            "strategy": strategy_name,
            "trades": total,
            "win_rate": wr,
            "pf": pf,
            "avg_r": avg_r,
            "total_pnl": total_pnl,
            "pnl_pct": pnl_pct,
            "mdd": mdd * 100,
        }
