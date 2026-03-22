"""WebSocket stream for real-time kline data from Binance."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Any, Callable

import pandas as pd
import websockets

from config import settings

logger = logging.getLogger(__name__)


class BinanceStream:
    """WebSocket client for real-time candle streaming with circular buffer."""

    def __init__(
        self,
        symbols: list[str],
        interval: str = settings.DEFAULT_TIMEFRAME,
        buffer_size: int = 1000,
        on_candle: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.symbols = [s.lower() for s in symbols]
        self.interval = interval
        self.buffer_size = buffer_size
        self.on_candle = on_candle
        self._buffers: dict[str, deque[dict[str, Any]]] = {
            s.upper(): deque(maxlen=buffer_size) for s in self.symbols
        }
        self._running = False
        self._ws: Any = None

    def _build_url(self) -> str:
        streams = [f"{s}@kline_{self.interval}" for s in self.symbols]
        return f"{settings.BINANCE_WS_URL}/{'/'.join(streams)}"

    async def _connect(self) -> None:
        url = self._build_url()
        reconnect_delay = 1.0

        while self._running:
            try:
                async with websockets.connect(url) as ws:
                    self._ws = ws
                    reconnect_delay = 1.0
                    logger.info("WebSocket connected: %s", url)

                    async for message in ws:
                        if not self._running:
                            break
                        self._handle_message(message)

            except (websockets.exceptions.ConnectionClosed, OSError) as e:
                logger.warning("WebSocket disconnected: %s, reconnecting in %.1fs", e, reconnect_delay)
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60.0)

    def _handle_message(self, raw: str) -> None:
        data = json.loads(raw)

        # Combined stream format
        if "data" in data:
            data = data["data"]

        if data.get("e") != "kline":
            return

        kline = data["k"]
        symbol = kline["s"]
        is_closed = kline["x"]

        candle = {
            "timestamp": int(kline["t"]),
            "open": float(kline["o"]),
            "high": float(kline["h"]),
            "low": float(kline["l"]),
            "close": float(kline["c"]),
            "volume": float(kline["v"]),
            "close_time": int(kline["T"]),
            "quote_volume": float(kline["q"]),
            "trades": int(kline["n"]),
        }

        if is_closed:
            self._buffers[symbol].append(candle)
            logger.debug("Closed candle for %s at %d", symbol, candle["timestamp"])
            if self.on_candle:
                self.on_candle(symbol, candle)

    def get_buffer_df(self, symbol: str) -> pd.DataFrame:
        """Get current buffer as a DataFrame for a given symbol."""
        buf = self._buffers.get(symbol.upper(), deque())
        if not buf:
            return pd.DataFrame()
        return pd.DataFrame(list(buf))

    async def start(self) -> None:
        """Start the WebSocket stream."""
        self._running = True
        await self._connect()

    async def stop(self) -> None:
        """Stop the WebSocket stream."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("WebSocket stream stopped")

    def run(self) -> None:
        """Blocking run for the WebSocket stream."""
        self._running = True
        asyncio.run(self._connect())
