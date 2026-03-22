"""Run all 5 intraday strategies on a symbol and compare results.

Usage:
    python scripts/run_all_strategies.py --symbol SPX500 --days 59
    python scripts/run_all_strategies.py --symbol BTCUSDT --days 180
"""
from __future__ import annotations

import argparse
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-30s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("strategy_compare")

from backtest.data_helper import fetch_15m_data
from backtest.multi_strategy import MultiStrategyBacktester
from strategies.intraday.s1_orb import generate_signals as gen_orb
from strategies.intraday.s2_london_ny_overlap import generate_signals as gen_overlap
from strategies.intraday.s3_pdhl_sweep import generate_signals as gen_pdhl
from strategies.intraday.s4_fvg_fill import generate_signals as gen_fvg
from strategies.intraday.s5_po3_simple import generate_signals as gen_po3


STRATEGIES = [
    ("1. ORB (Opening Range Breakout)", gen_orb),
    ("2. London-NY Overlap Momentum", gen_overlap),
    ("3. PDH/PDL Sweep Reversal", gen_pdhl),
    ("4. FVG Fill (Mean Reversion)", gen_fvg),
    ("5. PO3 Simplified", gen_po3),
]


def main():
    parser = argparse.ArgumentParser(description="Compare 5 intraday strategies")
    parser.add_argument("--symbol", required=True, help="SPX500, NDX100, BTCUSDT, etc.")
    parser.add_argument("--days", type=int, default=59, help="Days of history (max 59 for Yahoo)")
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--risk", type=float, default=0.02, help="Risk per trade (default 2%%)")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    days = args.days

    logger.info("Fetching %d days of 15m data for %s...", days, symbol)
    df = fetch_15m_data(symbol, days)
    if df.empty:
        logger.error("No data for %s", symbol)
        return

    logger.info("Got %d candles for %s", len(df), symbol)

    bt = MultiStrategyBacktester(
        initial_capital=args.capital,
        risk_per_trade=args.risk,
    )

    results = []
    for name, gen_fn in STRATEGIES:
        logger.info("Running %s...", name)
        try:
            signals = gen_fn(df, symbol)
            logger.info("  Generated %d signals", len(signals))
            trades = bt.run(df, signals)
            summary = bt.print_report(trades, name, symbol, days)
            results.append(summary)
        except Exception as e:
            logger.error("  Error in %s: %s", name, e, exc_info=True)
            results.append({"strategy": name, "trades": 0, "error": str(e)})

    # Print comparison table
    print()
    print("=" * 90)
    print(f"  STRATEGY COMPARISON  |  {symbol}  |  {days} days  |  Capital: ${args.capital:,.0f}")
    print("=" * 90)
    print(f"  {'Strategy':<35} {'Trades':>6} {'WR%':>5} {'PF':>6} {'AvgR':>6} {'PnL$':>10} {'PnL%':>7} {'MDD%':>6}")
    print("-" * 90)

    for r in results:
        if r.get("trades", 0) == 0:
            print(f"  {r['strategy']:<35} {'0':>6} {'--':>5} {'--':>6} {'--':>6} {'--':>10} {'--':>7} {'--':>6}")
            continue

        pnl_sign = "+" if r["total_pnl"] >= 0 else ""
        print(
            f"  {r['strategy']:<35} {r['trades']:>6} {r['win_rate']:>4.0f}% {r['pf']:>5.2f} "
            f"{r['avg_r']:>+5.2f} {pnl_sign}{r['total_pnl']:>9.2f} {pnl_sign}{r['pnl_pct']:>6.1f}% "
            f"{r['mdd']:>5.1f}%"
        )

    print("=" * 90)

    # Rank by PnL
    ranked = sorted(
        [r for r in results if r.get("trades", 0) > 0],
        key=lambda x: x.get("total_pnl", 0),
        reverse=True,
    )
    if ranked:
        best = ranked[0]
        print(f"\n  BEST: {best['strategy']} -> ${best['total_pnl']:+.2f} ({best['pnl_pct']:+.1f}%)")
    print()


if __name__ == "__main__":
    main()
