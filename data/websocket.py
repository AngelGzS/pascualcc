"""Binance kline WebSocket client for real-time candle data."""
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
