"""REST API kline poller — fallback when WebSocket is blocked (e.g. BingX 403).

Polls the BingX REST API every interval for the latest closed 15m candle.
Implements the same interface as BingXKlineWS (on_candle callback).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

import requests

logger = logging.getLogger(__name__)

# BingX symbol format: BTCUSDT -> BTC-USDT
_SYMBOL_MAP = {}


def _to_bingx_symbol(pair: str) -> str:
    if pair in _SYMBOL_MAP:
        return _SYMBOL_MAP[pair]
    # BTCUSDT -> BTC-USDT
    for base_len in (3, 4, 5, 6, 7):
        base = pair[:base_len]
        quote = pair[base_len:]
        if quote in ("USDT", "BUSD", "USD"):
            sym = f"{base}-{quote}"
            _SYMBOL_MAP[pair] = sym
            return sym
    return pair


# Map timeframe string to seconds
_TF_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400,
}


class BingXKlinePoller:
    """Polls BingX REST API for kline data as WebSocket fallback."""

    def __init__(
        self,
        pair: str,
        timeframe: str = "15m",
        on_candle: Callable | None = None,
        base_url: str = "https://open-api.bingx.com",
        poll_interval: float = 15.0,
    ):
        self.pair = pair
        self.symbol = _to_bingx_symbol(pair)
        self.timeframe = timeframe
        self.on_candle = on_candle
        self.base_url = base_url
        self.poll_interval = poll_interval
        self._running = False
        self._last_candle_ts: int = 0
        self._tf_seconds = _TF_SECONDS.get(timeframe, 900)

    def _fetch_latest_candles(self, limit: int = 3) -> list[dict] | None:
        """Fetch latest candles from BingX REST API."""
        url = f"{self.base_url}/openApi/swap/v3/quote/klines"
        params = {
            "symbol": self.symbol,
            "interval": self.timeframe,
            "limit": limit,
        }
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            result = data.get("data")
            if result:
                if self._last_candle_ts == 0:
                    logger.info("[Poller] First response: %d items, type=%s, sample=%s",
                                len(result), type(result[0]).__name__ if result else "?",
                                str(result[0])[:200] if result else "?")
                return result
        except Exception as e:
            logger.warning("[Poller] REST error: %s", e)
        return None

    def _candle_to_dict(self, raw: dict) -> dict:
        """Convert BingX kline format to standard dict."""
        return {
            "timestamp": int(raw["time"]),
            "open": float(raw["open"]),
            "high": float(raw["high"]),
            "low": float(raw["low"]),
            "close": float(raw["close"]),
            "volume": float(raw.get("volume", 0)),
        }

    async def start(self) -> None:
        """Start polling loop."""
        self._running = True
        logger.info(
            "[Poller] Starting REST poller for %s %s (every %.0fs)",
            self.pair, self.timeframe, self.poll_interval,
        )

        while self._running:
            try:
                candles = await asyncio.get_event_loop().run_in_executor(
                    None, self._fetch_latest_candles, 3
                )

                if candles:
                    # Handle case where data might be wrapped differently
                    if isinstance(candles, str):
                        logger.warning("[Poller] Got string instead of list, skipping")
                        await asyncio.sleep(self.poll_interval)
                        continue

                    # Ensure we have dicts
                    if candles and not isinstance(candles[0], dict):
                        logger.warning("[Poller] Unexpected candle format: %s", type(candles[0]))
                        await asyncio.sleep(self.poll_interval)
                        continue

                    # BingX returns newest first, we want oldest first
                    candles.sort(key=lambda x: int(x.get("time", 0)))

                    for raw in candles:
                        ts = int(raw.get("time", 0))
                        if ts == 0:
                            continue
                        # Only process candles we haven't seen
                        if ts > self._last_candle_ts:
                            # Check if this candle is closed
                            # A candle is closed if current time > candle_ts + tf_seconds
                            now_ms = int(time.time() * 1000)
                            candle_end_ms = ts + self._tf_seconds * 1000
                            if now_ms >= candle_end_ms:
                                candle = self._candle_to_dict(raw)
                                self._last_candle_ts = ts
                                if self.on_candle:
                                    result = self.on_candle(candle)
                                    if asyncio.iscoroutine(result):
                                        await result
                                    logger.debug(
                                        "[Poller] New closed candle: %s %.2f",
                                        self.pair, candle["close"],
                                    )

            except Exception as e:
                logger.error("[Poller] Error: %s (%s)", e, type(e).__name__, exc_info=True)

            await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        logger.info("[Poller] Stopped for %s", self.pair)
