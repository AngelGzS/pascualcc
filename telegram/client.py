"""Telethon client for reading signals from a Telegram channel/group."""
from __future__ import annotations

import logging
from typing import Any, Callable

from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat

from config import settings

logger = logging.getLogger(__name__)


class TelegramListener:
    """Connects to Telegram as a user and listens for new messages in a channel."""

    def __init__(
        self,
        channel_name: str,
        on_message: Callable[[str, int, int | None], None],
    ) -> None:
        """
        Args:
            channel_name: Name or ID of the Telegram channel/group.
            on_message: Callback(text, timestamp_ms, reply_to_msg_id).
        """
        self.channel_name = channel_name
        self.on_message = on_message
        self._client: TelegramClient | None = None
        self._channel_entity: Any = None

    async def start(self) -> None:
        """Connect to Telegram and start listening for messages."""
        api_id = settings.TELEGRAM_API_ID
        api_hash = settings.TELEGRAM_API_HASH

        if not api_id or not api_hash:
            raise RuntimeError(
                "Set TELEGRAM_API_ID and TELEGRAM_API_HASH env vars. "
                "Get them from https://my.telegram.org"
            )

        self._client = TelegramClient(
            settings.TELEGRAM_SESSION_NAME,
            int(api_id),
            api_hash,
        )

        await self._client.start()
        logger.info("Connected to Telegram as %s", (await self._client.get_me()).username)

        # Resolve channel
        self._channel_entity = await self._resolve_channel()
        if self._channel_entity is None:
            raise RuntimeError(f"Could not find channel/group: {self.channel_name}")

        channel_title = getattr(self._channel_entity, "title", self.channel_name)
        logger.info("Listening to: %s", channel_title)

        # Register message handler
        @self._client.on(events.NewMessage(chats=self._channel_entity))
        async def handler(event: events.NewMessage.Event) -> None:
            text = event.raw_text
            if not text:
                return

            timestamp_ms = int(event.date.timestamp() * 1000)
            reply_to = event.reply_to_msg_id

            logger.debug("New message in %s: %s", channel_title, text[:80])
            self.on_message(text, timestamp_ms, reply_to)

        await self._client.run_until_disconnected()

    async def _resolve_channel(self) -> Any:
        """Find the channel/group entity by name or ID."""
        try:
            # Try as username or invite link
            entity = await self._client.get_entity(self.channel_name)
            return entity
        except Exception:
            pass

        # Search in dialogs
        async for dialog in self._client.iter_dialogs():
            if dialog.name and dialog.name.lower() == self.channel_name.lower():
                return dialog.entity

        # Try as numeric ID
        try:
            entity = await self._client.get_entity(int(self.channel_name))
            return entity
        except (ValueError, Exception):
            pass

        return None

    async def fetch_history(self, days: int = 90) -> list[dict[str, Any]]:
        """Fetch message history from the channel.

        Returns list of dicts with keys: text, timestamp_ms, msg_id, reply_to_msg_id.
        """
        from datetime import datetime, timedelta, timezone

        api_id = settings.TELEGRAM_API_ID
        api_hash = settings.TELEGRAM_API_HASH

        if not api_id or not api_hash:
            raise RuntimeError(
                "Set TELEGRAM_API_ID and TELEGRAM_API_HASH env vars."
            )

        self._client = TelegramClient(
            settings.TELEGRAM_SESSION_NAME,
            int(api_id),
            api_hash,
        )

        await self._client.start()
        logger.info("Connected to Telegram")

        self._channel_entity = await self._resolve_channel()
        if self._channel_entity is None:
            raise RuntimeError(f"Could not find channel/group: {self.channel_name}")

        channel_title = getattr(self._channel_entity, "title", self.channel_name)
        logger.info("Fetching history from: %s (last %d days)", channel_title, days)

        offset_date = datetime.now(timezone.utc) - timedelta(days=days)
        messages: list[dict[str, Any]] = []

        async for msg in self._client.iter_messages(
            self._channel_entity,
            offset_date=offset_date,
            reverse=True,  # Oldest first
        ):
            if not msg.raw_text:
                continue
            messages.append({
                "text": msg.raw_text,
                "timestamp_ms": int(msg.date.timestamp() * 1000),
                "msg_id": msg.id,
                "reply_to_msg_id": getattr(msg.reply_to, "reply_to_msg_id", None)
                    if msg.reply_to else None,
            })

        logger.info("Fetched %d messages from %s", len(messages), channel_title)
        await self._client.disconnect()
        return messages

    async def stop(self) -> None:
        """Disconnect from Telegram."""
        if self._client:
            await self._client.disconnect()
