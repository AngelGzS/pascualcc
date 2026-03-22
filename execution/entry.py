"""Entry trailing logic for position entry."""
from __future__ import annotations

import logging

from config import settings
from config.types import Position, PositionState, ScoredSignal

logger = logging.getLogger(__name__)


class EntryTrailer:
    """Manages entry trailing for pending positions."""

    def __init__(
        self,
        entry_factor: float = settings.ENTRY_FACTOR,
        entry_timeout: int = settings.ENTRY_TIMEOUT,
    ) -> None:
        self.entry_factor = entry_factor
        self.entry_timeout = entry_timeout

    def create_pending_position(
        self,
        scored_signal: ScoredSignal,
        current_price: float,
        atr: float,
    ) -> Position:
        """Create a new position in PENDING_ENTRY state with entry trailing."""
        direction = scored_signal.signal.direction

        if direction == "long":
            entry_trigger = current_price - (atr * self.entry_factor)
        else:
            entry_trigger = current_price + (atr * self.entry_factor)

        pos = Position(
            pair=scored_signal.signal.pair,
            direction=direction,
            state=PositionState.PENDING_ENTRY,
            signal=scored_signal,
            entry_trigger=entry_trigger,
            entry_timeout_remaining=self.entry_timeout,
            atr_at_entry=atr,
        )

        logger.info(
            "Pending %s entry for %s: trigger=%.4f, timeout=%d candles",
            direction, pos.pair, entry_trigger, self.entry_timeout,
        )
        return pos

    def update_trailing(self, pos: Position, current_price: float, atr: float) -> Position:
        """Update the entry trailing trigger each candle.

        For long: trigger trails up (never down)
        For short: trigger trails down (never up)
        """
        if pos.state != PositionState.PENDING_ENTRY:
            return pos

        pos.entry_timeout_remaining -= 1

        if pos.entry_timeout_remaining <= 0:
            pos.state = PositionState.CANCELLED
            logger.info("Entry trailing cancelled for %s (timeout)", pos.pair)
            return pos

        if pos.direction == "long":
            new_trigger = current_price - (atr * self.entry_factor)
            if new_trigger > pos.entry_trigger:
                pos.entry_trigger = new_trigger
        else:
            new_trigger = current_price + (atr * self.entry_factor)
            if new_trigger < pos.entry_trigger:
                pos.entry_trigger = new_trigger

        return pos

    def check_entry(self, pos: Position, candle_low: float, candle_high: float) -> bool:
        """Check if entry trigger has been hit during this candle.

        For long: price touches trigger from above (low <= trigger)
        For short: price touches trigger from below (high >= trigger)
        """
        if pos.state != PositionState.PENDING_ENTRY:
            return False

        if pos.direction == "long":
            return candle_low <= pos.entry_trigger
        else:
            return candle_high >= pos.entry_trigger
