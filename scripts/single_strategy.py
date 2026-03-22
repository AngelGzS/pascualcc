"""Run a single strategy and show detailed trade log."""
from __future__ import annotations

import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)

from datetime import datetime, timezone
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


STRATEGIES = {
    "orb": gen_orb,
    "orb-enhanced": gen_orb_v2,
    "orb-tight": gen_orb_tight,
    "london-ny": gen_overlap,
    "pdhl": gen_pdhl,
    "fvg": gen_fvg,
    "po3": gen_po3,
    "vwap": gen_vwap,
}


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


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run a single strategy with detailed trade log")
    parser.add_argument("--strategy", required=True, choices=list(STRATEGIES.keys()),
                        help="Strategy to run")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading pair")
    parser.add_argument("--days", type=int, default=180, help="Days of data")
    parser.add_argument("--capital", type=float, default=10000, help="Initial capital")
    parser.add_argument("--rr", type=float, default=2.5, help="R:R target (e.g. 1.5, 2.0, 2.5)")
    parser.add_argument("--risk", type=float, default=0.02, help="Risk per trade (default 0.02)")
    parser.add_argument("--compound", action="store_true", help="Compound risk (pct of current capital instead of initial)")
    args = parser.parse_args()

    risk_mode = "compound" if args.compound else "fixed"
    print(f"\n{'=' * 75}")
    print(f"  {args.strategy.upper()} | {args.symbol} | {args.days}d | ${args.capital:,.0f} | {args.rr}R | {args.risk:.0%} risk ({risk_mode})")
    print(f"{'=' * 75}")

    df = fetch_15m_data(args.symbol.upper(), args.days)
    if df.empty:
        print("  No data found.")
        return
    print(f"  {len(df)} candles loaded\n")

    gen_fn = STRATEGIES[args.strategy]
    sigs = gen_fn(df, args.symbol.upper())
    sigs = adjust_tp(sigs, args.rr)

    bt = MultiStrategyBacktester(
        initial_capital=args.capital,
        risk_per_trade=args.risk,
        fixed_risk=not args.compound,
    )
    trades = bt.run(df, sigs)

    if not trades:
        print("  No trades generated.")
        return

    # Summary
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd <= 0]
    n = len(trades)
    wr = len(wins) / n * 100
    total_pnl = sum(t.pnl_usd for t in trades)
    gross_p = sum(t.pnl_usd for t in wins)
    gross_l = abs(sum(t.pnl_usd for t in losses))
    pf = gross_p / gross_l if gross_l > 0 else float("inf")
    avg_r = sum(t.r_multiple for t in trades) / n

    eq = args.capital
    peak = eq
    mdd = 0.0
    for t in trades:
        eq += t.pnl_usd
        peak = max(peak, eq)
        dd = (peak - eq) / peak
        mdd = max(mdd, dd)

    print(f"  Trades:         {n}")
    print(f"  Win rate:       {wr:.0f}%")
    print(f"  Profit factor:  {pf:.2f}")
    print(f"  Avg R:R:        {avg_r:+.2f}")
    print(f"  Total PnL:      ${total_pnl:+,.2f} ({total_pnl / args.capital * 100:+.1f}%)")
    print(f"  Max drawdown:   {mdd * 100:.1f}%")
    print(f"  Avg winner:     ${(gross_p / len(wins)):,.2f}" if wins else "")
    print(f"  Avg loser:      ${(gross_l / len(losses) * -1):,.2f}" if losses else "")
    print(f"{'-' * 75}")
    print(f"  Trade log:")

    eq = args.capital
    for t in trades:
        eq += t.pnl_usd
        entry_dt = datetime.fromtimestamp(t.entry_time / 1000, tz=timezone.utc)
        exit_dt = datetime.fromtimestamp(t.exit_time / 1000, tz=timezone.utc)
        tag = "[W]" if t.pnl_usd > 0 else "[L]"
        dur_h = (t.exit_time - t.entry_time) / 1000 / 3600

        print(
            f"    {tag} {entry_dt.strftime('%m-%d %H:%M')} "
            f"{'LONG ' if t.signal.direction == 'long' else 'SHORT'} "
            f"entry={t.fill_price:>10.2f} "
            f"exit={t.exit_price:>10.2f} "
            f"({t.exit_reason:<7}) "
            f"${t.pnl_usd:>+9.2f} ({t.r_multiple:>+.1f}R) "
            f"dur={dur_h:.1f}h "
            f"eq=${eq:>10,.2f}"
        )

    # Monthly breakdown
    print(f"\n{'-' * 75}")
    print(f"  Monthly breakdown:")
    monthly: dict[str, list] = {}
    for t in trades:
        dt = datetime.fromtimestamp(t.entry_time / 1000, tz=timezone.utc)
        key = dt.strftime("%Y-%m")
        monthly.setdefault(key, []).append(t)

    for month in sorted(monthly.keys()):
        mtrades = monthly[month]
        mpnl = sum(t.pnl_usd for t in mtrades)
        mwins = sum(1 for t in mtrades if t.pnl_usd > 0)
        mwr = mwins / len(mtrades) * 100
        print(f"    {month}: {len(mtrades):>3} trades | WR {mwr:>4.0f}% | PnL ${mpnl:>+9.2f}")

    print(f"{'=' * 75}\n")


if __name__ == "__main__":
    main()
