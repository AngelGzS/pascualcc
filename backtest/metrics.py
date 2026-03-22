"""Performance metrics for backtesting."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config.types import TradeRecord

logger = logging.getLogger(__name__)


@dataclass
class BacktestMetrics:
    """Complete metrics for a backtest run."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl_usd: float = 0.0
    total_pnl_percent: float = 0.0
    avg_win_usd: float = 0.0
    avg_loss_usd: float = 0.0
    avg_rr_ratio: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_usd: float = 0.0
    calmar_ratio: float = 0.0
    sharpe_ratio: float = 0.0
    annualized_return: float = 0.0
    avg_duration_candles: float = 0.0
    max_consecutive_losses: int = 0
    avg_mfe: float = 0.0
    avg_mae: float = 0.0


def calculate_metrics(
    trades: list[TradeRecord],
    initial_capital: float,
    trading_days: int = 0,
) -> BacktestMetrics:
    """Calculate all performance metrics from a list of trades."""
    m = BacktestMetrics()

    if not trades:
        return m

    m.total_trades = len(trades)

    pnls = [t.pnl_usd for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    m.winning_trades = len(wins)
    m.losing_trades = len(losses)
    m.win_rate = m.winning_trades / m.total_trades if m.total_trades > 0 else 0.0

    m.total_pnl_usd = sum(pnls)
    m.total_pnl_percent = m.total_pnl_usd / initial_capital if initial_capital > 0 else 0.0

    m.avg_win_usd = np.mean(wins) if wins else 0.0
    m.avg_loss_usd = abs(np.mean(losses)) if losses else 0.0

    m.avg_rr_ratio = m.avg_win_usd / m.avg_loss_usd if m.avg_loss_usd > 0 else 0.0

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    m.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

    # Max drawdown
    equity_curve = _build_equity_curve(trades, initial_capital)
    m.max_drawdown, m.max_drawdown_usd = _calculate_max_drawdown(equity_curve)

    # Annualized return and Calmar
    if trading_days > 0 and trading_days > 1:
        total_return = m.total_pnl_usd / initial_capital
        annual_factor = 365.0 / trading_days
        m.annualized_return = ((1 + total_return) ** annual_factor - 1) if total_return > -1 else -1.0
    else:
        m.annualized_return = m.total_pnl_percent

    m.calmar_ratio = m.annualized_return / m.max_drawdown if m.max_drawdown > 0 else 0.0

    # Sharpe ratio (using trade returns, annualized)
    if len(pnls) > 1:
        returns = np.array([t.pnl_percent for t in trades])
        avg_return = np.mean(returns)
        std_return = np.std(returns, ddof=1)
        if std_return > 0:
            # DECISION: Annualize with sqrt(trades_per_year), approximate 96 candles/day * 365
            trades_per_year = m.total_trades * (365.0 / max(trading_days, 1)) if trading_days > 0 else m.total_trades
            m.sharpe_ratio = (avg_return / std_return) * math.sqrt(max(trades_per_year, 1))

    # Duration
    m.avg_duration_candles = np.mean([t.duration_candles for t in trades])

    # Consecutive losses
    m.max_consecutive_losses = _max_consecutive_losses(pnls)

    # Excursions
    m.avg_mfe = np.mean([t.max_favorable_excursion for t in trades])
    m.avg_mae = np.mean([t.max_adverse_excursion for t in trades])

    return m


def _build_equity_curve(trades: list[TradeRecord], initial_capital: float) -> list[float]:
    """Build equity curve from trade PnLs."""
    curve = [initial_capital]
    equity = initial_capital
    for t in trades:
        equity += t.pnl_usd
        curve.append(equity)
    return curve


def _calculate_max_drawdown(equity_curve: list[float]) -> tuple[float, float]:
    """Calculate maximum drawdown as (percentage, absolute_usd)."""
    if len(equity_curve) < 2:
        return 0.0, 0.0

    peak = equity_curve[0]
    max_dd_pct = 0.0
    max_dd_usd = 0.0

    for equity in equity_curve:
        if equity > peak:
            peak = equity
        dd_usd = peak - equity
        dd_pct = dd_usd / peak if peak > 0 else 0.0

        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            max_dd_usd = dd_usd

    return max_dd_pct, max_dd_usd


def _max_consecutive_losses(pnls: list[float]) -> int:
    """Find maximum consecutive losing trades."""
    max_streak = 0
    current_streak = 0
    for p in pnls:
        if p <= 0:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0
    return max_streak


def format_report(
    metrics: BacktestMetrics,
    pair: str,
    timeframe: str,
    period_start: str,
    period_end: str,
    params: dict | None = None,
) -> str:
    """Format a human-readable backtest report."""
    lines = [
        "=== BACKTEST REPORT ===",
        f"Par: {pair} | Timeframe: {timeframe}",
        f"Periodo: {period_start} a {period_end}",
        "",
        "--- Resultados ---",
        f"Total trades:       {metrics.total_trades}",
        f"Win rate:           {metrics.win_rate:.1%}",
        f"Profit factor:      {metrics.profit_factor:.2f}",
        f"Avg R:R:            {metrics.avg_rr_ratio:.1f}:1",
        f"Total PnL:          ${metrics.total_pnl_usd:.2f} ({metrics.total_pnl_percent:.1%})",
        f"Max drawdown:       {metrics.max_drawdown:.1%} (${metrics.max_drawdown_usd:.2f})",
        f"Calmar ratio:       {metrics.calmar_ratio:.2f}",
        f"Sharpe ratio:       {metrics.sharpe_ratio:.2f}",
        f"Avg duration:       {metrics.avg_duration_candles:.1f} candles",
        f"Max consec losses:  {metrics.max_consecutive_losses}",
    ]

    if params:
        lines.append("")
        lines.append("--- Parametros ---")
        for k, v in params.items():
            lines.append(f"{k}: {v}")

    return "\n".join(lines)
