"""Kline WebSocket clients for real-time candle data (Binance + BingX)."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

import websockets
from websockets.exceptions import ConnectionClosed

from config import settings

logger = logging.getLogger(__name__)

# Binance WebSocket base URL
WS_BASE = "wss://stream.binance.com/ws"


def _to_bingx_symbol(symbol: str) -> str:
    """Convert Binance format (BTCUSDT) to BingX format (BTC-USDT)."""
    if "-" in symbol:
        return symbol
    for quote in ("USDT", "USDC", "BUSD"):
        if symbol.endswith(quote):
            base = symbol[: -len(quote)]
            return f"{base}-{quote}"
    return symbol


class BinanceKlineWS:
    """Connects to Binance kline stream and fires callback on candle close.

    Only triggers when kline.is_final == True (candle fully closed),
    keeping behavior identical to backtesting on historical OHLCV data.
    """

    def __init__(
        self,
        pair: str,
        timeframe: str,
        on_candle: Callable[[dict[str, Any]], None],
    ) -> None:
        self.pair = pair.lower()
        self.timeframe = timeframe
        self.on_candle = on_candle
        self._ws: Any = None
        self._running = False
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0

    @property
    def url(self) -> str:
        return f"{WS_BASE}/{self.pair}@kline_{self.timeframe}"

    async def start(self) -> None:
        """Connect and listen forever, reconnecting on failure."""
        self._running = True
        while self._running:
            try:
                await self._connect_and_listen()
            except (ConnectionClosed, OSError) as e:
                if not self._running:
                    break
                logger.warning(
                    "WebSocket disconnected: %s. Reconnecting in %.0fs...",
                    e, self._reconnect_delay,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay,
                )
            except Exception as e:
                if not self._running:
                    break
                logger.error("Unexpected WS error: %s. Reconnecting in %.0fs...", e, self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay,
                )

    async def _connect_and_listen(self) -> None:
        """Single connection lifecycle."""
        logger.info("Connecting to %s", self.url)
        async with websockets.connect(self.url, ping_interval=20, ping_timeout=10) as ws:
            self._ws = ws
            self._reconnect_delay = 1.0  # Reset on successful connect
            logger.info("WebSocket connected: %s %s", self.pair.upper(), self.timeframe)

            async for raw_msg in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw_msg)
                    self._handle_message(msg)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from WS: %s", raw_msg[:100])

    def _handle_message(self, msg: dict[str, Any]) -> None:
        """Parse kline message, fire callback only on candle close."""
        kline = msg.get("k")
        if kline is None:
            return

        # Only process when candle is final (closed)
        if not kline.get("x", False):
            return

        candle = {
            "timestamp": int(kline["t"]),      # Open time
            "open": float(kline["o"]),
            "high": float(kline["h"]),
            "low": float(kline["l"]),
            "close": float(kline["c"]),
            "volume": float(kline["v"]),
            "close_time": int(kline["T"]),
            "quote_volume": float(kline["q"]),
            "trades": int(kline["n"]),
        }

        logger.debug(
            "Candle closed: %s O=%.2f H=%.2f L=%.2f C=%.2f",
            self.pair.upper(), candle["open"], candle["high"],
            candle["low"], candle["close"],
        )

        self.on_candle(candle)

    def stop(self) -> None:
        """Signal the listener to stop."""
        self._running = False
        if self._ws:
            asyncio.ensure_future(self._ws.close())


class BingXKlineWS:
    """Connects to BingX kline stream and fires callback on candle close.

    BingX WebSocket protocol:
    - Connect to wss://open-api-ws.bingx.com/swap-market
    - Send subscription: {"id": ..., "reqType": "sub", "dataType": "<symbol>@kline_<interval>"}
    - Respond to pings with pong to keep connection alive
    - Candle data arrives in {"data": {"E": ..., "K": {...}}} format
    """

    def __init__(
        self,
        pair: str,
        timeframe: str,
        on_candle: Callable[[dict[str, Any]], None],
    ) -> None:
        self.pair = pair
        self.bingx_symbol = _to_bingx_symbol(pair.upper())
        self.timeframe = timeframe
        self.on_candle = on_candle
        self._ws: Any = None
        self._running = False
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0

    @property
    def url(self) -> str:
        return settings.BINGX_WS_URL

    @property
    def _subscribe_msg(self) -> str:
        data_type = f"{self.bingx_symbol}@kline_{self.timeframe}"
        return json.dumps({"id": "kline_sub", "reqType": "sub", "dataType": data_type})

    async def start(self) -> None:
        """Connect and listen forever, reconnecting on failure."""
        self._running = True
        while self._running:
            try:
                await self._connect_and_listen()
            except (ConnectionClosed, OSError) as e:
                if not self._running:
                    break
                logger.warning(
                    "[BingX WS] Disconnected: %s. Reconnecting in %.0fs...",
                    e, self._reconnect_delay,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay,
                )
            except Exception as e:
                if not self._running:
                    break
                logger.error("[BingX WS] Unexpected error: %s. Reconnecting in %.0fs...", e, self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay,
                )

    async def _connect_and_listen(self) -> None:
        """Single connection lifecycle."""
        logger.info("[BingX WS] Connecting to %s", self.url)
        async with websockets.connect(self.url, ping_interval=20, ping_timeout=10) as ws:
            self._ws = ws
            self._reconnect_delay = 1.0

            # Subscribe to kline stream
            await ws.send(self._subscribe_msg)
            logger.info("[BingX WS] Subscribed: %s %s", self.bingx_symbol, self.timeframe)

            async for raw_msg in ws:
                if not self._running:
                    break
                try:
                    # BingX sends "Ping" as plain text, respond with "Pong"
                    if raw_msg == "Ping":
                        await ws.send("Pong")
                        continue

                    msg = json.loads(raw_msg)
                    await self._handle_message(msg)
                except json.JSONDecodeError:
                    logger.warning("[BingX WS] Invalid JSON: %s", str(raw_msg)[:100])

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        """Parse BingX kline message, fire callback only on candle close.

        BingX kline event format:
        {
          "dataType": "BTC-USDT@kline_15m",
          "data": {
            "E": 1234567890,  // event time
            "K": {
              "t": 1234567800000,  // kline start time
              "T": 1234568700000,  // kline close time
              "s": "BTC-USDT",
              "i": "15m",
              "o": "50000.0",
              "h": "50100.0",
              "l": "49900.0",
              "c": "50050.0",
              "v": "100.5",
              "x": true  // is this kline closed?
            }
          }
        }
        """
        data = msg.get("data")
        if data is None:
            return

        kline = data.get("K") if isinstance(data, dict) else None
        if kline is None:
            return

        # Only process when candle is final (closed)
        if not kline.get("x", False):
            return

        # Produce candle dict with same keys as Binance WS for compatibility
        candle = {
            "t": int(kline["t"]),
            "o": str(kline["o"]),
            "h": str(kline["h"]),
            "l": str(kline["l"]),
            "c": str(kline["c"]),
            "v": str(kline["v"]),
        }

        logger.debug(
            "[BingX WS] Candle closed: %s O=%s H=%s L=%s C=%s",
            self.bingx_symbol, candle["o"], candle["h"],
            candle["l"], candle["c"],
        )

        # Support both sync and async callbacks
        result = self.on_candle(candle)
        if asyncio.iscoroutine(result):
            await result

    def stop(self) -> None:
        """Signal the listener to stop."""
        self._running = False
        if self._ws:
            asyncio.ensure_future(self._ws.close())
