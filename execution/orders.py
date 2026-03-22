"""Interface with Binance for order execution (live trading)."""
from __future__ import annotations

import logging
import time
from typing import Any

from config import settings

logger = logging.getLogger(__name__)


class OrderManager:
    """Manages order placement and tracking on Binance.

    For backtesting, this module is NOT used. The backtest engine simulates
    fills directly. This is for live/paper trading only.
    """

    def __init__(self) -> None:
        self._client: Any = None

    def connect(self) -> None:
        """Initialize the Binance client."""
        try:
            from binance.client import Client
            self._client = Client(
                settings.BINANCE_API_KEY,
                settings.BINANCE_API_SECRET,
            )
            logger.info("Connected to Binance API")
        except ImportError:
            logger.error("python-binance not installed")
            raise
        except Exception as e:
            logger.error("Failed to connect to Binance: %s", e)
            raise

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
    ) -> dict[str, Any]:
        """Place a limit order."""
        if self._client is None:
            raise RuntimeError("OrderManager not connected. Call connect() first.")

        try:
            order = self._client.create_order(
                symbol=symbol,
                side=side.upper(),
                type="LIMIT",
                timeInForce="GTC",
                quantity=f"{quantity:.8f}",
                price=f"{price:.8f}",
            )
            logger.info(
                "Limit order placed: %s %s %.8f @ %.8f, id=%s",
                side, symbol, quantity, price, order.get("orderId"),
            )
            return order
        except Exception as e:
            logger.error("Failed to place limit order: %s", e)
            raise

    def place_stop_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float,
    ) -> dict[str, Any]:
        """Place a stop-market order (for stop loss on futures)."""
        if self._client is None:
            raise RuntimeError("OrderManager not connected. Call connect() first.")

        try:
            order = self._client.futures_create_order(
                symbol=symbol,
                side=side.upper(),
                type="STOP_MARKET",
                stopPrice=f"{stop_price:.8f}",
                quantity=f"{quantity:.8f}",
            )
            logger.info(
                "Stop market order placed: %s %s %.8f, stop=%.8f, id=%s",
                side, symbol, quantity, stop_price, order.get("orderId"),
            )
            return order
        except Exception as e:
            logger.error("Failed to place stop market order: %s", e)
            raise

    def cancel_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        """Cancel an open order."""
        if self._client is None:
            raise RuntimeError("OrderManager not connected. Call connect() first.")

        try:
            result = self._client.cancel_order(symbol=symbol, orderId=order_id)
            logger.info("Order cancelled: %s %d", symbol, order_id)
            return result
        except Exception as e:
            logger.error("Failed to cancel order: %s", e)
            raise

    def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        """Get trading rules for a symbol (min qty, tick size, etc.)."""
        if self._client is None:
            raise RuntimeError("OrderManager not connected. Call connect() first.")

        info = self._client.get_symbol_info(symbol)
        return info if info else {}

    def notify_telegram(self, message: str, urgency: str = "normal") -> None:
        """Send a notification via Telegram."""
        if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
            logger.debug("Telegram not configured, skipping notification")
            return

        import requests as req

        prefix = {"critical": "\U0001f534", "high": "\u26a0\ufe0f"}.get(urgency, "\u2139\ufe0f")
        try:
            req.post(
                f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": settings.TELEGRAM_CHAT_ID, "text": f"{prefix} {message}"},
                timeout=10,
            )
        except Exception as e:
            logger.warning("Failed to send Telegram notification: %s", e)
