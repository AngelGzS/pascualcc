"""Optimize strategy parameters by sweeping TP multipliers and entry filters.

Tests each strategy with different R:R targets to find optimal.
"""
from __future__ import annotations

import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)

import pandas as pd
from backtest.data_helper import fetch_15m_data
from backtest.multi_strategy import MultiStrategyBacktester, SimpleSignal

# Import all strategy generators
from strategies.intraday.s1_orb import generate_signals as gen_orb_base, STRATEGY_NAME as ORB_NAME
from strategies.intraday.s2_london_ny_overlap import generate_signals as gen_overlap_base
from strategies.intraday.s3_pdhl_sweep import generate_signals as gen_pdhl_base
from strategies.intraday.s4_fvg_fill import generate_signals as gen_fvg_base
from strategies.intraday.s5_po3_simple import generate_signals as gen_po3_base


def adjust_tp(signals: list[SimpleSignal], tp_rr: float) -> list[SimpleSignal]:
    """Adjust TP of all signals to a given R:R multiple."""
    adjusted = []
    for s in signals:
        risk = abs(s.entry_price - s.stop_loss)
        if risk <= 0:
            continue
        if s.direction == "long":
            new_tp = s.entry_price + risk * tp_rr
        else:
            new_tp = s.entry_price - risk * tp_rr
        adjusted.append(SimpleSignal(
            timestamp=s.timestamp,
            bar_index=s.bar_index,
            symbol=s.symbol,
            direction=s.direction,
            entry_price=s.entry_price,
            stop_loss=s.stop_loss,
            take_profit=new_tp,
            strategy_name=s.strategy_name,
            entry_type=s.entry_type,
            metadata=s.metadata,
        ))
    return adjusted


def evaluate(bt, df, signals, name):
    """Quick evaluate without printing."""
    trades = bt.run(df, signals)
    n = len(trades)
    if n == 0:
        return None
    wins = sum(1 for t in trades if t.pnl_usd > 0)
    wr = wins / n * 100
    total_pnl = sum(t.pnl_usd for t in trades)
    gross_p = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
    gross_l = abs(sum(t.pnl_usd for t in trades if t.pnl_usd <= 0))
    pf = gross_p / gross_l if gross_l > 0 else float("inf")
    avg_r = sum(t.r_multiple for t in trades) / n
    return {"name": name, "trades": n, "wr": wr, "pf": pf, "avg_r": avg_r, "pnl": total_pnl}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--days", type=int, default=59)
    args = parser.parse_args()

    symbol = args.symbol.upper()
    print(f"\nFetching {args.days}d of 15m data for {symbol}...")
    df = fetch_15m_data(symbol, args.days)
    if df.empty:
        print("No data!")
        return
    print(f"Got {len(df)} candles\n")

    bt = MultiStrategyBacktester(initial_capital=10_000, risk_per_trade=0.02)

    # Generate base signals for each strategy
    strategies = {
        "ORB": gen_orb_base(df, symbol),
        "London-NY": gen_overlap_base(df, symbol),
        "PDH/PDL": gen_pdhl_base(df, symbol),
        "FVG Fill": gen_fvg_base(df, symbol),
        "PO3": gen_po3_base(df, symbol),
    }

    # Test each with different TP multipliers
    tp_rrs = [1.0, 1.5, 2.0, 2.5, 3.0]

    print(f"{'Strategy':<15} {'TP_RR':>5} {'Trades':>6} {'WR%':>5} {'PF':>5} {'AvgR':>6} {'PnL$':>10}")
    print("-" * 60)

    best_overall = None
    best_pnl = -999999

    for strat_name, base_signals in strategies.items():
        if not base_signals:
            print(f"{strat_name:<15} {'--':>5} {'0':>6}")
            continue

        for rr in tp_rrs:
            adjusted = adjust_tp(base_signals, rr)
            result = evaluate(bt, df, adjusted, f"{strat_name}_{rr}R")
            if result is None:
                continue

            marker = ""
            if result["pnl"] > best_pnl:
                best_pnl = result["pnl"]
                best_overall = result
                marker = " <-- BEST"

            pnl_sign = "+" if result["pnl"] >= 0 else ""
            print(
                f"{strat_name:<15} {rr:>5.1f} {result['trades']:>6} "
                f"{result['wr']:>4.0f}% {result['pf']:>5.2f} {result['avg_r']:>+5.2f} "
                f"{pnl_sign}{result['pnl']:>9.2f}{marker}"
            )

    print("-" * 60)
    if best_overall:
        print(f"\nBEST CONFIG: {best_overall['name']}")
        print(f"  Trades={best_overall['trades']} WR={best_overall['wr']:.0f}% PF={best_overall['pf']:.2f} PnL=${best_overall['pnl']:+.2f}")
    print()


if __name__ == "__main__":
    main()
