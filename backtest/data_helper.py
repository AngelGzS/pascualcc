"""Helper to fetch 15m OHLCV data from Yahoo Finance or Binance."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

logger = logging.getLogger(__name__)


def fetch_15m_data(symbol: str, days: int) -> pd.DataFrame:
    """Fetch 15m data for a symbol. Auto-selects source.

    Returns DataFrame with columns: timestamp, open, high, low, close, volume
    timestamp is Unix ms.
    """
    YAHOO_MAP = {
        "SPX500": "ES=F",
        "NDX100": "NQ=F",
        "DOWJONES": "YM=F",
        "GOLD": "GC=F",
        "EURUSD": "EURUSD=X",
        "GBPUSD": "GBPUSD=X",
    }

    upper = symbol.upper()

    if upper in YAHOO_MAP:
        return _fetch_yahoo(YAHOO_MAP[upper], days)
    elif upper.endswith("USDT") and "-" not in upper:
        return _fetch_binance(upper, days)
    else:
        logger.error("Unknown symbol: %s", symbol)
        return pd.DataFrame()


def _fetch_yahoo(ticker: str, days: int) -> pd.DataFrame:
    import yfinance as yf

    period = f"{min(days, 59)}d"
    logger.info("Fetching %s from Yahoo Finance (%s)", ticker, period)
    raw = yf.download(ticker, period=period, interval="15m", progress=False)
    if raw.empty:
        return pd.DataFrame()

    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [c[0] for c in raw.columns]

    ts_ms = raw.index.map(lambda x: int(x.timestamp() * 1000))
    return pd.DataFrame({
        "timestamp": ts_ms,
        "open": raw["Open"].values,
        "high": raw["High"].values,
        "low": raw["Low"].values,
        "close": raw["Close"].values,
        "volume": raw["Volume"].values,
    }).reset_index(drop=True)


def _fetch_binance(symbol: str, days: int) -> pd.DataFrame:
    from data.fetcher import BinanceFetcher

    fetcher = BinanceFetcher()
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=days)).timestamp() * 1000)

    logger.info("Fetching %s from Binance (%d days)", symbol, days)
    return fetcher.fetch_all_klines(symbol, "15m", start_ms, end_ms)
