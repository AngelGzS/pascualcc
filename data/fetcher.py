"""Exchange API clients for fetching historical OHLCV data (Binance + BingX fallback)."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests

from config import settings

logger = logging.getLogger(__name__)


# ─── Interval mapping: Binance format → BingX format ─────────────────────────
_BINGX_INTERVAL_MAP: dict[str, str] = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "12h": "12h",
    "1d": "1d", "1w": "1w", "1M": "1M",
}


class BinanceFetcher:
    """Fetches historical kline data from Binance REST API with rate limiting."""

    EXCHANGE_NAME = "Binance"

    def __init__(self) -> None:
        self._session = requests.Session()
        self._last_request_time: float = 0.0
        self._min_interval: float = 60.0 / settings.MAX_REQUESTS_PER_MINUTE

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()

    def _request_with_backoff(self, url: str, params: dict[str, Any], max_retries: int = 5) -> list[list[Any]]:
        backoff = settings.BACKOFF_BASE_SECONDS
        retries = 0
        while retries < max_retries:
            self._rate_limit()
            try:
                resp = self._session.get(url, params=params, timeout=30)
                # 400 = bad symbol / invalid params — don't retry
                if resp.status_code == 400:
                    logger.warning("[%s] Bad request (400) for %s — skipping", self.EXCHANGE_NAME, params.get("symbol", "?"))
                    return []
                if resp.status_code == 429 or resp.status_code == 418:
                    logger.warning("Rate limited (status %d), backing off %.1fs", resp.status_code, backoff)
                    time.sleep(backoff)
                    backoff = min(backoff * 2, settings.BACKOFF_MAX_SECONDS)
                    retries += 1
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                logger.error("Request error: %s, backing off %.1fs", e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, settings.BACKOFF_MAX_SECONDS)
                retries += 1
        logger.error("Max retries (%d) exceeded for %s", max_retries, params.get("symbol", "?"))
        return []

    def fetch_klines(
        self,
        symbol: str,
        interval: str = settings.DEFAULT_TIMEFRAME,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = settings.KLINE_LIMIT,
    ) -> pd.DataFrame:
        """Fetch a single batch of klines (up to 1000)."""
        url = f"{settings.BINANCE_BASE_URL}/api/v3/klines"
        params: dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms

        raw = self._request_with_backoff(url, params)
        return self._parse_klines(raw)

    def fetch_all_klines(
        self,
        symbol: str,
        interval: str = settings.DEFAULT_TIMEFRAME,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> pd.DataFrame:
        """Fetch all klines between start_ms and end_ms, paginating automatically."""
        all_frames: list[pd.DataFrame] = []
        current_start = start_ms

        while True:
            df = self.fetch_klines(
                symbol=symbol,
                interval=interval,
                start_ms=current_start,
                end_ms=end_ms,
                limit=settings.KLINE_LIMIT,
            )
            if df.empty:
                break

            all_frames.append(df)
            last_ts = int(df["timestamp"].iloc[-1])
            last_dt = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            logger.info(
                "Fetched %d candles for %s, last: %s",
                len(df), symbol, last_dt,
            )

            if len(df) < settings.KLINE_LIMIT:
                break

            # Move start to after the last candle's close_time
            current_start = int(df["close_time"].iloc[-1]) + 1
            if end_ms is not None and current_start > end_ms:
                break

        if not all_frames:
            return pd.DataFrame()

        result = pd.concat(all_frames, ignore_index=True)
        result = result.drop_duplicates(subset="timestamp", keep="last").reset_index(drop=True)
        return result

    @staticmethod
    def _parse_klines(raw: list[list[Any]]) -> pd.DataFrame:
        """Parse raw Binance kline response into a clean DataFrame."""
        if not raw:
            return pd.DataFrame()

        records = []
        for k in raw:
            records.append({
                "timestamp": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": int(k[6]),
                "quote_volume": float(k[7]),
                "trades": int(k[8]),
            })
        return pd.DataFrame(records)

    def get_exchange_info(self, symbol: str) -> dict[str, Any]:
        """Get exchange info for a symbol (min qty, tick size, etc.)."""
        url = f"{settings.BINANCE_BASE_URL}/api/v3/exchangeInfo"
        params = {"symbol": symbol}
        self._rate_limit()
        resp = self._session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for s in data.get("symbols", []):
            if s["symbol"] == symbol:
                return s
        return {}

    def get_ticker_price(self, symbol: str) -> float:
        """Get current price for a symbol."""
        url = f"{settings.BINANCE_BASE_URL}/api/v3/ticker/price"
        params = {"symbol": symbol}
        self._rate_limit()
        resp = self._session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return float(resp.json()["price"])


class BingXFetcher:
    """Fetches historical kline data from BingX REST API (fallback exchange)."""

    EXCHANGE_NAME = "BingX"
    MAX_LIMIT = 1440  # BingX max per request

    def __init__(self) -> None:
        self._session = requests.Session()
        self._last_request_time: float = 0.0
        self._min_interval: float = 60.0 / settings.BINGX_MAX_REQUESTS_PER_MINUTE
        self._symbol_market: dict[str, str] = {}  # Cache: symbol → "spot" or "swap"

    @staticmethod
    def _to_bingx_symbol(symbol: str) -> str:
        """Convert Binance format (BTCUSDT) to BingX format (BTC-USDT)."""
        if "-" in symbol:
            return symbol
        # Strip common quote assets
        for quote in ("USDT", "USDC", "BUSD"):
            if symbol.endswith(quote):
                base = symbol[: -len(quote)]
                return f"{base}-{quote}"
        return symbol

    @staticmethod
    def _to_bingx_interval(interval: str) -> str:
        """Convert Binance interval format to BingX format."""
        return _BINGX_INTERVAL_MAP.get(interval, interval)

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()

    def _request_with_backoff(self, url: str, params: dict[str, Any], max_retries: int = 5) -> dict[str, Any] | list:
        backoff = settings.BACKOFF_BASE_SECONDS
        retries = 0
        while retries < max_retries:
            self._rate_limit()
            try:
                resp = self._session.get(url, params=params, timeout=30)
                if resp.status_code == 400:
                    logger.warning("[%s] Bad request (400) for %s — skipping", self.EXCHANGE_NAME, params.get("symbol", "?"))
                    return {}
                if resp.status_code == 429:
                    logger.warning("[BingX] Rate limited, backing off %.1fs", backoff)
                    time.sleep(backoff)
                    backoff = min(backoff * 2, settings.BACKOFF_MAX_SECONDS)
                    retries += 1
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                logger.error("[BingX] Request error: %s, backing off %.1fs", e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, settings.BACKOFF_MAX_SECONDS)
                retries += 1
        logger.error("[BingX] Max retries (%d) exceeded for %s", max_retries, params.get("symbol", "?"))
        return {}

    def _try_endpoint(self, url: str, params: dict[str, Any]) -> pd.DataFrame:
        """Try a single BingX endpoint and parse the result."""
        result = self._request_with_backoff(url, params)

        # BingX wraps data in {"code": 0, "data": [...]}
        if isinstance(result, dict):
            code = result.get("code", -1)
            if code != 0:
                return pd.DataFrame()
            raw = result.get("data", [])
        else:
            raw = result

        return self._parse_klines(raw)

    def fetch_klines(
        self,
        symbol: str,
        interval: str = settings.DEFAULT_TIMEFRAME,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """Fetch a single batch of klines from BingX (spot first, then perpetual futures)."""
        bingx_symbol = self._to_bingx_symbol(symbol)
        bingx_interval = self._to_bingx_interval(interval)
        actual_limit = min(limit or self.MAX_LIMIT, self.MAX_LIMIT)

        params: dict[str, Any] = {
            "symbol": bingx_symbol,
            "interval": bingx_interval,
            "limit": actual_limit,
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms

        # Perpetual futures only (signals are futures trades — no spot fallback)
        url_swap = f"{settings.BINGX_BASE_URL}/openApi/swap/v3/quote/klines"
        df = self._try_endpoint(url_swap, params)
        if not df.empty:
            if not self._symbol_market.get(symbol):
                logger.info("[BingX] %s found on perpetual futures", bingx_symbol)
            self._symbol_market[symbol] = "swap"
            return df

        return pd.DataFrame()

    def fetch_all_klines(
        self,
        symbol: str,
        interval: str = settings.DEFAULT_TIMEFRAME,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> pd.DataFrame:
        """Fetch all klines between start_ms and end_ms, paginating automatically."""
        all_frames: list[pd.DataFrame] = []
        current_start = start_ms

        while True:
            df = self.fetch_klines(
                symbol=symbol,
                interval=interval,
                start_ms=current_start,
                end_ms=end_ms,
                limit=self.MAX_LIMIT,
            )
            if df.empty:
                break

            all_frames.append(df)
            last_ts = int(df["timestamp"].iloc[-1])
            last_dt = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            logger.info(
                "[BingX] Fetched %d candles for %s, last: %s",
                len(df), symbol, last_dt,
            )

            if len(df) < self.MAX_LIMIT:
                break

            current_start = int(df["close_time"].iloc[-1]) + 1
            if end_ms is not None and current_start > end_ms:
                break

        if not all_frames:
            return pd.DataFrame()

        result = pd.concat(all_frames, ignore_index=True)
        result = result.drop_duplicates(subset="timestamp", keep="last").reset_index(drop=True)
        return result

    @staticmethod
    def _parse_klines(raw: list[Any]) -> pd.DataFrame:
        """Parse BingX kline response. BingX returns dicts or arrays depending on endpoint."""
        if not raw:
            return pd.DataFrame()

        records = []
        for k in raw:
            # BingX spot v2 returns list of dicts
            if isinstance(k, dict):
                records.append({
                    "timestamp": int(k.get("time", k.get("openTime", 0))),
                    "open": float(k.get("open", 0)),
                    "high": float(k.get("high", 0)),
                    "low": float(k.get("low", 0)),
                    "close": float(k.get("close", 0)),
                    "volume": float(k.get("volume", 0)),
                    "close_time": int(k.get("time", k.get("closeTime", 0))),
                    "quote_volume": float(k.get("quoteVolume", 0)),
                    "trades": 0,
                })
            # Array format (similar to Binance)
            elif isinstance(k, (list, tuple)) and len(k) >= 6:
                records.append({
                    "timestamp": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "close_time": int(k[6]) if len(k) > 6 else int(k[0]),
                    "quote_volume": float(k[7]) if len(k) > 7 else 0.0,
                    "trades": int(k[8]) if len(k) > 8 else 0,
                })

        if not records:
            return pd.DataFrame()

        return pd.DataFrame(records)


class FallbackFetcher:
    """Tries Binance first, falls back to BingX if symbol not found.

    Transparent drop-in replacement for BinanceFetcher — same interface.
    """

    def __init__(self) -> None:
        self._binance = BinanceFetcher()
        self._bingx = BingXFetcher()
        # Cache which symbols are on which exchange to avoid re-checking
        self._symbol_exchange: dict[str, str] = {}

    def fetch_all_klines(
        self,
        symbol: str,
        interval: str = settings.DEFAULT_TIMEFRAME,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> pd.DataFrame:
        """Fetch klines, trying Binance first then BingX."""
        cached = self._symbol_exchange.get(symbol)

        # If we already know it's on BingX, go straight there
        if cached == "bingx":
            return self._bingx.fetch_all_klines(symbol, interval, start_ms, end_ms)

        # Try Binance first
        df = self._binance.fetch_all_klines(symbol, interval, start_ms, end_ms)
        if not df.empty:
            self._symbol_exchange[symbol] = "binance"
            return df

        # Binance failed — try BingX
        logger.info("[Fallback] %s not found on Binance, trying BingX...", symbol)
        df = self._bingx.fetch_all_klines(symbol, interval, start_ms, end_ms)
        if not df.empty:
            self._symbol_exchange[symbol] = "bingx"
            logger.info("[Fallback] %s found on BingX ✓", symbol)
            return df

        logger.warning("[Fallback] %s not found on Binance or BingX — skipping", symbol)
        return pd.DataFrame()

    def fetch_klines(
        self,
        symbol: str,
        interval: str = settings.DEFAULT_TIMEFRAME,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = settings.KLINE_LIMIT,
    ) -> pd.DataFrame:
        """Fetch single batch, trying Binance first then BingX."""
        cached = self._symbol_exchange.get(symbol)

        if cached == "bingx":
            return self._bingx.fetch_klines(symbol, interval, start_ms, end_ms, limit)

        df = self._binance.fetch_klines(symbol, interval, start_ms, end_ms, limit)
        if not df.empty:
            self._symbol_exchange[symbol] = "binance"
            return df

        logger.info("[Fallback] %s not found on Binance, trying BingX...", symbol)
        df = self._bingx.fetch_klines(symbol, interval, start_ms, end_ms, limit)
        if not df.empty:
            self._symbol_exchange[symbol] = "bingx"
            return df

        return pd.DataFrame()

    # Delegate other methods to Binance (primary)
    def get_exchange_info(self, symbol: str) -> dict[str, Any]:
        return self._binance.get_exchange_info(symbol)

    def get_ticker_price(self, symbol: str) -> float:
        return self._binance.get_ticker_price(symbol)
