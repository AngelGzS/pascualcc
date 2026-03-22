"""Console dashboard for paper trading — live status output."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any

from config.types import Position, PositionState, TradeRecord


def print_dashboard(
    pair: str,
    timeframe: str,
    capital: float,
    initial_capital: float,
    positions: list[Position],
    trades: list[TradeRecord],
    start_time: int,
    candles_processed: int,
    last_price: float,
    last_signal_info: str,
) -> None:
    """Print a compact live dashboard to the console."""
    # Clear screen for clean output
    if sys.platform == "win32":
        os.system("cls")
    else:
        print("\033[2J\033[H", end="")

    pnl_pct = ((capital - initial_capital) / initial_capital) * 100
    pnl_sign = "+" if pnl_pct >= 0 else ""

    # Uptime
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    uptime_s = (now_ms - start_time) // 1000 if start_time > 0 else 0
    days = uptime_s // 86400
    hours = (uptime_s % 86400) // 3600
    mins = (uptime_s % 3600) // 60
    if days > 0:
        uptime_str = f"{days}d {hours}h"
    elif hours > 0:
        uptime_str = f"{hours}h {mins}m"
    else:
        uptime_str = f"{mins}m"

    # Trade stats
    n_trades = len(trades)
    if n_trades > 0:
        wins = sum(1 for t in trades if t.pnl_usd > 0)
        wr = wins / n_trades * 100
        gross_profit = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
        gross_loss = abs(sum(t.pnl_usd for t in trades if t.pnl_usd < 0))
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Max drawdown from equity curve of trades
        peak = initial_capital
        max_dd = 0.0
        equity = initial_capital
        for t in trades:
            equity += t.pnl_usd
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        stats_str = f"Trades: {n_trades} | WR: {wr:.0f}% | PF: {pf:.2f} | MDD: {max_dd:.1%}"
    else:
        stats_str = "Trades: 0 | Waiting for signals..."

    # Open positions
    open_positions = [
        p for p in positions
        if p.state in (PositionState.OPEN, PositionState.PARTIAL_TP, PositionState.PENDING_ENTRY)
    ]

    # Header
    print(f"{'=' * 60}")
    print(f"  PAPER TRADING  |  {pair} {timeframe}  |  Candles: {candles_processed}")
    print(f"{'=' * 60}")
    print(f"  Capital: ${capital:,.2f} ({pnl_sign}{pnl_pct:.2f}%)  |  Uptime: {uptime_str}")
    print(f"  Price:   ${last_price:,.2f}")
    print(f"  {stats_str}")
    print(f"{'-' * 60}")

    # Open positions
    if open_positions:
        for p in open_positions:
            state_label = p.state.value.upper()
            if p.state == PositionState.PENDING_ENTRY:
                print(
                    f"  [{state_label}] {p.direction.upper()} "
                    f"trigger=${p.entry_trigger:,.2f} "
                    f"(timeout: {p.entry_timeout_remaining})"
                )
            else:
                unrealized = _unrealized_pnl(p, last_price)
                print(
                    f"  [{state_label}] {p.direction.upper()} "
                    f"@ ${p.entry_price:,.2f}  "
                    f"SL=${p.stop_loss:,.2f}  TP=${p.take_profit:,.2f}  "
                    f"PnL=${unrealized:+,.2f}"
                )
    else:
        print("  No open positions")

    print(f"{'-' * 60}")

    # Last signal
    print(f"  {last_signal_info}")

    # Recent trades (last 3)
    if trades:
        print(f"{'-' * 60}")
        print("  Recent trades:")
        for t in trades[-3:]:
            ts = datetime.fromtimestamp(t.exit_time / 1000, tz=timezone.utc).strftime("%m-%d %H:%M") if t.exit_time > 0 else "?"
            print(
                f"    {ts} {t.direction.upper()} "
                f"${t.pnl_usd:+.2f} ({t.pnl_percent:+.2%}) "
                f"[{t.exit_reason}]"
            )

    print(f"{'=' * 60}")
    print("  Ctrl+C to stop and save state")


def _unrealized_pnl(pos: Position, current_price: float) -> float:
    """Calculate unrealized PnL for an open position."""
    if pos.direction == "long":
        return (current_price - pos.entry_price) * pos.size
    else:
        return (pos.entry_price - current_price) * pos.size
