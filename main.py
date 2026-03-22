"""Entry point: connects all layers for backtesting and live trading."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

from config import settings
from config.pairs import PAIRS, TIMEFRAME
from data.fetcher import BinanceFetcher
from data.storage import ParquetStorage, interval_to_ms
from indicators.calculator import calculate_all_indicators
from backtest.engine import BacktestEngine
from backtest.walk_forward import WalkForwardOptimizer, format_walk_forward_report
from backtest.metrics import format_report

logger = logging.getLogger("trading_bot")


def setup_logging() -> None:
    """Configure logging for the entire application."""
    os.makedirs(settings.LOG_DIR, exist_ok=True)
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(name)-25s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)

    # File handler
    file_handler = logging.FileHandler(
        os.path.join(settings.LOG_DIR, "trading_bot.log"),
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)


def cmd_download(args: argparse.Namespace) -> None:
    """Download historical data from Binance."""
    fetcher = BinanceFetcher()
    storage = ParquetStorage()

    pairs = args.pairs if args.pairs else PAIRS
    interval = args.timeframe or TIMEFRAME

    # Calculate start time
    days = args.days or 180  # Default 6 months
    end_ms = int(datetime.utcnow().timestamp() * 1000)
    start_ms = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)

    for pair in pairs:
        logger.info("Downloading %s %s, last %d days...", pair, interval, days)

        # Check for existing data for incremental download
        last_ts = storage.get_last_timestamp(pair, interval)
        actual_start = last_ts + 1 if last_ts else start_ms

        df = fetcher.fetch_all_klines(
            symbol=pair,
            interval=interval,
            start_ms=actual_start,
            end_ms=end_ms,
        )

        if df.empty:
            logger.warning("No new data for %s", pair)
            continue

        storage.save(df, pair, interval)

        # Check for gaps
        interval_ms = interval_to_ms(interval)
        full_df = storage.load(pair, interval)
        gaps = storage.detect_gaps(full_df, interval_ms)
        if gaps:
            logger.warning("%d gaps detected in %s data", len(gaps), pair)

        logger.info("Done: %s — %d total candles stored", pair, len(full_df))


def cmd_backtest(args: argparse.Namespace) -> None:
    """Run backtest for a single pair."""
    storage = ParquetStorage()
    pair = args.pair or PAIRS[0]
    interval = args.timeframe or TIMEFRAME

    df = storage.load(pair, interval)
    if df.empty:
        logger.error("No data for %s. Run 'download' first.", pair)
        return

    logger.info("Running backtest on %s: %d candles", pair, len(df))

    # Calculate indicators
    df = calculate_all_indicators(df)

    engine = BacktestEngine()
    trades = engine.run(df, pair)
    metrics = engine.get_metrics(trading_days=len(df) // 96)  # Approx for 15m

    report = format_report(
        metrics, pair, interval,
        str(pd.Timestamp(df["timestamp"].iloc[0], unit="ms")),
        str(pd.Timestamp(df["timestamp"].iloc[-1], unit="ms")),
    )
    print(report)


def cmd_walk_forward(args: argparse.Namespace) -> None:
    """Run walk-forward optimization."""
    storage = ParquetStorage()
    pair = args.pair or PAIRS[0]
    interval = args.timeframe or TIMEFRAME

    df = storage.load(pair, interval)
    if df.empty:
        logger.error("No data for %s. Run 'download' first.", pair)
        return

    logger.info("Running walk-forward on %s: %d candles", pair, len(df))

    optimizer = WalkForwardOptimizer(
        n_trials=args.trials or settings.OPTUNA_N_TRIALS,
    )
    result = optimizer.run(df, pair)

    report = format_walk_forward_report(result, pair, interval)
    print(report)


def cmd_copy_backtest(args: argparse.Namespace) -> None:
    """Backtest Telegram signals from channel history."""
    from telegram.backtester import SignalBacktester

    backtester = SignalBacktester(initial_capital=args.capital, risk_per_trade=args.risk, leverage_mult=args.leverage_mult)
    asyncio.run(backtester.run(channel_name=args.channel, days=args.days))


def cmd_copy(args: argparse.Namespace) -> None:
    """Run live copy-trading from Telegram signals (paper mode)."""
    from telegram.copy_executor import CopyTradeExecutor

    executor = CopyTradeExecutor(
        channel_name=args.channel,
        resume=args.resume,
    )
    asyncio.run(executor.start())


def cmd_po3_backtest(args: argparse.Namespace) -> None:
    """Backtest the PO3 strategy on index/commodity 15m data from BingX."""
    from strategies.po3 import settings as po3_settings
    from backtest.po3_engine import PO3BacktestEngine
    from data.fetcher import BinanceFetcher, BingXFetcher

    friendly = args.symbol.upper()
    api_symbol = po3_settings.INDEX_SYMBOLS.get(friendly)
    if api_symbol is None:
        available = ", ".join(sorted(po3_settings.INDEX_SYMBOLS.keys()))
        logger.error("Unknown symbol '%s'. Available: %s", friendly, available)
        return

    capital = args.capital if args.capital is not None else po3_settings.INITIAL_CAPITAL
    rr = args.rr if args.rr is not None else po3_settings.DEFAULT_RR
    days = args.days

    logger.info(
        "PO3 backtest: %s (%s), %d days, capital=$%.0f, RR=%.1f",
        friendly, api_symbol, days, capital, rr,
    )

    # Determine data source
    is_binance = api_symbol.endswith("USDT") and "-" not in api_symbol and not api_symbol.startswith("NC")
    # Yahoo Finance symbols (for backtest only — real indices)
    YAHOO_SYMBOLS = {
        "SPX500": "ES=F",       # E-mini S&P 500 futures (nearly 24h)
        "NDX100": "NQ=F",       # E-mini Nasdaq futures (nearly 24h)
        "DOWJONES": "YM=F",     # E-mini Dow futures
        "RUSSELL2000": "RTY=F", # E-mini Russell futures
        "GOLD": "GC=F",
        "OIL_WTI": "CL=F",
        "EURUSD": "EURUSD=X",
        "GBPUSD": "GBPUSD=X",
        "USDJPY": "JPY=X",
    }
    yahoo_ticker = YAHOO_SYMBOLS.get(friendly)

    if yahoo_ticker:
        # Use Yahoo Finance for real index data (backtest)
        import yfinance as yf
        logger.info("Using Yahoo Finance (%s) for backtest data", yahoo_ticker)
        period = f"{days}d" if days <= 59 else f"{min(days, 729)}d"
        raw = yf.download(yahoo_ticker, period=period, interval="15m", progress=False)
        if raw.empty:
            logger.error("No data from Yahoo Finance for %s", yahoo_ticker)
            return
        # Flatten multi-level columns
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = [c[0] for c in raw.columns]
        # Convert to our standard format (timestamp in Unix ms)
        ts_ms = raw.index.map(lambda x: int(x.timestamp() * 1000))
        df_15m = pd.DataFrame({
            "timestamp": ts_ms,
            "open": raw["Open"].values,
            "high": raw["High"].values,
            "low": raw["Low"].values,
            "close": raw["Close"].values,
            "volume": raw["Volume"].values,
        })
    elif is_binance:
        fetcher = BinanceFetcher()
        from datetime import timezone as tz
        end_ms = int(datetime.now(tz.utc).timestamp() * 1000)
        start_ms = int((datetime.now(tz.utc) - timedelta(days=days)).timestamp() * 1000)
        df_15m = fetcher.fetch_all_klines(symbol=api_symbol, interval="15m", start_ms=start_ms, end_ms=end_ms)
    else:
        fetcher = BingXFetcher()
        from datetime import timezone as tz
        end_ms = int(datetime.now(tz.utc).timestamp() * 1000)
        start_ms = int((datetime.now(tz.utc) - timedelta(days=days)).timestamp() * 1000)
        df_15m = fetcher.fetch_all_klines(symbol=api_symbol, interval="15m", start_ms=start_ms, end_ms=end_ms)

    if df_15m.empty:
        logger.error("No 15m data returned for %s. Check symbol or API.", friendly)
        return

    logger.info("Fetched %d 15m candles for %s", len(df_15m), friendly)

    engine = PO3BacktestEngine(
        initial_capital=capital,
        rr_target=rr,
    )
    trades = engine.run(df_15m, friendly)
    engine.print_report(trades, friendly, days=days)


def cmd_paper(args: argparse.Namespace) -> None:
    """Run paper trading with live Binance prices."""
    from execution.paper_executor import PaperExecutor

    pair = args.pair or PAIRS[0]
    timeframe = args.timeframe or TIMEFRAME

    executor = PaperExecutor(
        pair=pair,
        timeframe=timeframe,
        resume=args.resume,
    )
    asyncio.run(executor.start())


def main() -> None:
    """Main entry point."""
    setup_logging()

    parser = argparse.ArgumentParser(description="Trading Bot - Divergence Confluence System")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Download command
    dl_parser = subparsers.add_parser("download", help="Download historical data")
    dl_parser.add_argument("--pairs", nargs="+", help="Pairs to download (default: all configured)")
    dl_parser.add_argument("--timeframe", help=f"Timeframe (default: {TIMEFRAME})")
    dl_parser.add_argument("--days", type=int, help="Days of history (default: 180)")

    # Backtest command
    bt_parser = subparsers.add_parser("backtest", help="Run single backtest")
    bt_parser.add_argument("--pair", help=f"Pair to backtest (default: {PAIRS[0]})")
    bt_parser.add_argument("--timeframe", help=f"Timeframe (default: {TIMEFRAME})")

    # Walk-forward command
    wf_parser = subparsers.add_parser("walk-forward", help="Run walk-forward optimization")
    wf_parser.add_argument("--pair", help=f"Pair to optimize (default: {PAIRS[0]})")
    wf_parser.add_argument("--timeframe", help=f"Timeframe (default: {TIMEFRAME})")
    wf_parser.add_argument("--trials", type=int, help=f"Optuna trials (default: {settings.OPTUNA_N_TRIALS})")

    # Paper trading command
    paper_parser = subparsers.add_parser("paper", help="Paper trading (live prices, simulated orders)")
    paper_parser.add_argument("--pair", help=f"Pair to trade (default: {PAIRS[0]})")
    paper_parser.add_argument("--timeframe", help=f"Timeframe (default: {TIMEFRAME})")
    paper_parser.add_argument("--resume", action="store_true", help="Resume from saved state")

    # Copy-trade backtest (historical signals from Telegram)
    cb_parser = subparsers.add_parser("copy-backtest", help="Backtest Telegram signals from history")
    cb_parser.add_argument("--channel", required=True, help="Telegram channel/group name")
    cb_parser.add_argument("--days", type=int, default=90, help="Days of history (default: 90)")
    cb_parser.add_argument("--capital", type=float, default=settings.INITIAL_CAPITAL, help=f"Initial capital (default: {settings.INITIAL_CAPITAL})")
    cb_parser.add_argument("--risk", type=float, default=0.02, help="Risk per trade (default: 0.02 = 2%%)")
    cb_parser.add_argument("--leverage-mult", type=float, default=1.0, help="Leverage multiplier (default: 1.0 = use signal leverage)")

    # Copy-trade live (paper)
    copy_parser = subparsers.add_parser("copy", help="Copy-trade from Telegram signals (paper)")
    copy_parser.add_argument("--channel", required=True, help="Telegram channel/group name")
    copy_parser.add_argument("--resume", action="store_true", help="Resume from saved state")

    # PO3 backtest
    po3_parser = subparsers.add_parser("po3-backtest", help="Backtest PO3 strategy on index/commodity data")
    po3_parser.add_argument("--symbol", required=True, help="Friendly name (SPX500, NDX100, GOLD, etc.)")
    po3_parser.add_argument("--days", type=int, default=60, help="Days of history (default: 60)")
    po3_parser.add_argument("--capital", type=float, default=None, help="Initial capital (default: from po3 settings)")
    po3_parser.add_argument("--rr", type=float, default=None, help="R:R target (default: from po3 settings)")

    args = parser.parse_args()

    if args.command == "download":
        cmd_download(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    elif args.command == "walk-forward":
        cmd_walk_forward(args)
    elif args.command == "paper":
        cmd_paper(args)
    elif args.command == "copy-backtest":
        cmd_copy_backtest(args)
    elif args.command == "copy":
        cmd_copy(args)
    elif args.command == "po3-backtest":
        cmd_po3_backtest(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
