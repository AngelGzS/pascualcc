"""Entry point for paper trading (Docker + local).

Runs ORB paper executor + web dashboard concurrently.
Supports multiple pairs via comma-separated PAIRS env var.

Usage:
  python run_paper.py
  python run_paper.py --pair BTCUSDT --rr 2.5 --capital 10000
  PAIRS=BTCUSDT,ETHUSDT python run_paper.py
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(name)-30s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("paper_runner")


async def run(args: argparse.Namespace) -> None:
    from execution.orb_paper_executor import ORBPaperExecutor
    from web_api import start_web

    # Start web dashboard
    await start_web(port=args.port)

    # Determine pairs
    pairs_str = os.environ.get("PAIRS", args.pair)
    pairs = [p.strip().upper() for p in pairs_str.split(",")]

    # Launch one executor per pair
    tasks = []
    for pair in pairs:
        executor = ORBPaperExecutor(
            pair=pair,
            initial_capital=args.capital,
            risk_per_trade=args.risk,
            rr_target=args.rr,
            fixed_risk=not args.compound,
            resume=True,  # always resume in Docker
            strategy=args.strategy,
        )
        tasks.append(asyncio.create_task(executor.start()))
        logger.info("Launched executor for %s (strategy=%s, rr=%.1f)", pair, args.strategy, args.rr)

    await asyncio.gather(*tasks)


def main() -> None:
    parser = argparse.ArgumentParser(description="ORB Paper Trading")
    parser.add_argument("--pair", default="BTCUSDT", help="Pair(s), comma-separated or env PAIRS")
    parser.add_argument("--capital", type=float, default=float(os.environ.get("CAPITAL", "10000")))
    parser.add_argument("--risk", type=float, default=float(os.environ.get("RISK", "0.02")))
    parser.add_argument("--rr", type=float, default=float(os.environ.get("RR_TARGET", "2.5")))
    parser.add_argument("--strategy", default=os.environ.get("STRATEGY", "orb"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    parser.add_argument("--compound", action="store_true")
    args = parser.parse_args()

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
