"""Full strategy comparison: runs all strategies with optimized params on multiple symbols."""
from __future__ import annotations

import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)

import numpy as np
import pandas as pd
from backtest.data_helper import fetch_15m_data
from backtest.multi_strategy import MultiStrategyBacktester, SimpleSignal

from strategies.intraday.s1_orb import generate_signals as gen_orb
from strategies.intraday.s1_orb_v2 import generate_signals as gen_orb_v2
from strategies.intraday.s2_london_ny_overlap import generate_signals as gen_overlap
from strategies.intraday.s3_pdhl_sweep import generate_signals as gen_pdhl
from strategies.intraday.s4_fvg_fill import generate_signals as gen_fvg
from strategies.intraday.s5_po3_simple import generate_signals as gen_po3
from strategies.intraday.s6_orb_tight import generate_signals as gen_orb_tight
from strategies.intraday.s7_vwap_reversion import generate_signals as gen_vwap


def adjust_tp(signals: list[SimpleSignal], tp_rr: float) -> list[SimpleSignal]:
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
            timestamp=s.timestamp, bar_index=s.bar_index, symbol=s.symbol,
            direction=s.direction, entry_price=s.entry_price,
            stop_loss=s.stop_loss, take_profit=new_tp,
            strategy_name=s.strategy_name, entry_type=s.entry_type,
            metadata=s.metadata,
        ))
    return adjusted


def evaluate(bt, df, signals):
    trades = bt.run(df, signals)
    n = len(trades)
    if n == 0:
        return None
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd <= 0]
    wr = len(wins) / n * 100
    total_pnl = sum(t.pnl_usd for t in trades)
    gross_p = sum(t.pnl_usd for t in wins)
    gross_l = abs(sum(t.pnl_usd for t in losses))
    pf = gross_p / gross_l if gross_l > 0 else float("inf")
    avg_r = sum(t.r_multiple for t in trades) / n

    eq = bt.initial_capital
    peak = eq
    mdd = 0.0
    for t in trades:
        eq += t.pnl_usd
        peak = max(peak, eq)
        dd = (peak - eq) / peak
        mdd = max(mdd, dd)

    return {
        "trades": n, "wr": wr, "pf": pf, "avg_r": avg_r,
        "pnl": total_pnl, "pnl_pct": total_pnl / bt.initial_capital * 100,
        "mdd": mdd * 100,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT"])
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--capital", type=float, default=10000)
    args = parser.parse_args()

    CONFIGS = [
        # High WR targets (1.0R - 1.5R)
        ("ORB 1.0R", gen_orb, 1.0),
        ("ORB 1.5R", gen_orb, 1.5),
        ("ORB Enhanced 1.5R", gen_orb_v2, 1.5),
        ("London-NY 1.5R", gen_overlap, 1.5),
        ("PDH/PDL 1.5R", gen_pdhl, 1.5),
        ("FVG Fill 1.5R", gen_fvg, 1.5),
        ("PO3 Simple 1.5R", gen_po3, 1.5),
        ("VWAP 2-Sigma Rev", gen_vwap, None),
        # Medium R:R targets (2.0R)
        ("ORB 2.0R", gen_orb, 2.0),
        ("ORB Enhanced 2.0R", gen_orb_v2, 2.0),
        ("London-NY 2.0R", gen_overlap, 2.0),
        # Original high R:R
        ("ORB 2.5R", gen_orb, 2.5),
        ("ORB Enhanced", gen_orb_v2, None),  # already has 2.5R built in
        ("ORB 3.0R", gen_orb, 3.0),
    ]

    for symbol in args.symbols:
        print(f"\n{'#' * 90}")
        print(f"  {symbol} | {args.days} days | Capital: ${args.capital:,.0f}")
        print(f"{'#' * 90}")

        df = fetch_15m_data(symbol.upper(), args.days)
        if df.empty:
            print(f"  No data for {symbol}")
            continue
        print(f"  {len(df)} candles loaded\n")

        bt = MultiStrategyBacktester(initial_capital=args.capital, risk_per_trade=0.02)

        print(f"  {'Strategy':<25} {'Trades':>6} {'WR%':>5} {'PF':>6} {'AvgR':>6} {'PnL$':>10} {'PnL%':>7} {'MDD%':>6}")
        print(f"  {'-' * 75}")

        results = []
        for name, gen_fn, tp_rr in CONFIGS:
            try:
                sigs = gen_fn(df, symbol.upper())
                if tp_rr is not None:
                    sigs = adjust_tp(sigs, tp_rr)
                r = evaluate(bt, df, sigs)
                if r is None:
                    print(f"  {name:<25} {'0':>6}")
                    continue

                pnl_s = "+" if r["pnl"] >= 0 else ""
                star = " ***" if r["pnl"] > 0 and r["pf"] > 1.0 else ""
                print(
                    f"  {name:<25} {r['trades']:>6} {r['wr']:>4.0f}% {r['pf']:>5.2f} "
                    f"{r['avg_r']:>+5.2f} {pnl_s}{r['pnl']:>9.2f} {pnl_s}{r['pnl_pct']:>6.1f}% "
                    f"{r['mdd']:>5.1f}%{star}"
                )
                r["name"] = name
                results.append(r)
            except Exception as e:
                print(f"  {name:<25} ERROR: {e}")

        print(f"  {'-' * 75}")

        # Top strategies
        profitable = sorted([r for r in results if r["pnl"] > 0], key=lambda x: -x["pnl"])
        if profitable:
            print(f"\n  PROFITABLE STRATEGIES:")
            for i, r in enumerate(profitable, 1):
                print(f"    {i}. {r['name']} -> ${r['pnl']:+.2f} ({r['pnl_pct']:+.1f}%) | WR={r['wr']:.0f}% PF={r['pf']:.2f} MDD={r['mdd']:.1f}%")
        else:
            print(f"\n  No profitable strategies found for {symbol}")
        print()


if __name__ == "__main__":
    main()
