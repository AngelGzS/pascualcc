"""Exit management: stop loss, take profit, trailing stop."""
from __future__ import annotations

import logging

from config import settings
from config.types import Position, PositionState

logger = logging.getLogger(__name__)


class ExitManager:
    """Manages stop loss, take profit, and trailing stop for open positions."""

    def __init__(
        self,
        atr_multiplier: float = settings.ATR_MULTIPLIER,
        tp_multiplier: float = settings.TP_MULTIPLIER,
        partial_tp_ratio: float = settings.PARTIAL_TP_RATIO,
        partial_tp_atr: float = settings.PARTIAL_TP_ATR,
        trailing_activation_atr: float = settings.TRAILING_ACTIVATION_ATR,
    ) -> None:
        self.atr_multiplier = atr_multiplier
        self.tp_multiplier = tp_multiplier
        self.partial_tp_ratio = partial_tp_ratio
        self.partial_tp_atr = partial_tp_atr
        self.trailing_activation_atr = trailing_activation_atr

    def initialize_exit_levels(self, pos: Position) -> Position:
        """Set initial stop loss and take profit levels after entry."""
        atr = pos.atr_at_entry

        if pos.direction == "long":
            pos.stop_loss = pos.entry_price - (atr * self.atr_multiplier)
            pos.take_profit = pos.entry_price + (atr * self.tp_multiplier)
            pos.trailing_stop = pos.stop_loss
        else:
            pos.stop_loss = pos.entry_price + (atr * self.atr_multiplier)
            pos.take_profit = pos.entry_price - (atr * self.tp_multiplier)
            pos.trailing_stop = pos.stop_loss

        logger.info(
            "%s %s: entry=%.4f, SL=%.4f, TP=%.4f",
            pos.direction.upper(), pos.pair,
            pos.entry_price, pos.stop_loss, pos.take_profit,
        )
        return pos

    def check_exits(
        self,
        pos: Position,
        candle_high: float,
        candle_low: float,
        candle_close: float,
        current_atr: float,
    ) -> Position:
        """Check all exit conditions for an open position.

        Order of checks: stop loss first (worst case), then take profit,
        then trailing stop update.
        """
        if pos.state not in (PositionState.OPEN, PositionState.PARTIAL_TP):
            return pos

        pos.candles_in_trade += 1

        # Track excursions
        if pos.direction == "long":
            favorable = candle_high - pos.entry_price
            adverse = pos.entry_price - candle_low
        else:
            favorable = pos.entry_price - candle_low
            adverse = candle_high - pos.entry_price

        pos.max_favorable = max(pos.max_favorable, favorable)
        pos.max_adverse = max(pos.max_adverse, adverse)

        # --- Stop loss check ---
        if self._check_stop_loss(pos, candle_low, candle_high):
            return pos

        # --- Partial take profit ---
        if pos.state == PositionState.OPEN and not pos.partial_tp_done:
            if self._check_partial_tp(pos, candle_high, candle_low):
                return pos

        # --- Full take profit ---
        if self._check_take_profit(pos, candle_high, candle_low):
            return pos

        # --- Trailing stop update ---
        self._update_trailing_stop(pos, candle_close, current_atr)

        # --- Trailing stop hit ---
        if self._check_trailing_stop(pos, candle_low, candle_high):
            return pos

        return pos

    def _check_stop_loss(self, pos: Position, candle_low: float, candle_high: float) -> bool:
        if pos.direction == "long" and candle_low <= pos.stop_loss:
            pos.state = PositionState.CLOSED_SL
            logger.info("Stop loss hit for %s %s at %.4f", pos.direction, pos.pair, pos.stop_loss)
            return True
        if pos.direction == "short" and candle_high >= pos.stop_loss:
            pos.state = PositionState.CLOSED_SL
            logger.info("Stop loss hit for %s %s at %.4f", pos.direction, pos.pair, pos.stop_loss)
            return True
        return False

    def _check_partial_tp(self, pos: Position, candle_high: float, candle_low: float) -> bool:
        atr = pos.atr_at_entry
        if pos.direction == "long":
            partial_level = pos.entry_price + (atr * self.partial_tp_atr)
            if candle_high >= partial_level:
                pos.partial_tp_done = True
                pos.size = pos.original_size * (1.0 - self.partial_tp_ratio)
                pos.state = PositionState.PARTIAL_TP
                # Move stop to breakeven
                pos.stop_loss = pos.entry_price
                pos.trailing_stop = pos.entry_price
                logger.info("Partial TP for %s %s, moved SL to breakeven", pos.direction, pos.pair)
                return True
        else:
            partial_level = pos.entry_price - (atr * self.partial_tp_atr)
            if candle_low <= partial_level:
                pos.partial_tp_done = True
                pos.size = pos.original_size * (1.0 - self.partial_tp_ratio)
                pos.state = PositionState.PARTIAL_TP
                pos.stop_loss = pos.entry_price
                pos.trailing_stop = pos.entry_price
                logger.info("Partial TP for %s %s, moved SL to breakeven", pos.direction, pos.pair)
                return True
        return False

    def _check_take_profit(self, pos: Position, candle_high: float, candle_low: float) -> bool:
        if pos.direction == "long" and candle_high >= pos.take_profit:
            pos.state = PositionState.CLOSED_TP
            logger.info("Take profit hit for %s %s at %.4f", pos.direction, pos.pair, pos.take_profit)
            return True
        if pos.direction == "short" and candle_low <= pos.take_profit:
            pos.state = PositionState.CLOSED_TP
            logger.info("Take profit hit for %s %s at %.4f", pos.direction, pos.pair, pos.take_profit)
            return True
        return False

    def _update_trailing_stop(self, pos: Position, candle_close: float, current_atr: float) -> None:
        """Update trailing stop only after position is in profit by trailing_activation_atr."""
        atr = pos.atr_at_entry

        if pos.direction == "long":
            profit = candle_close - pos.entry_price
            if profit >= atr * self.trailing_activation_atr:
                new_trail = candle_close - (current_atr * self.atr_multiplier)
                if new_trail > pos.trailing_stop:
                    pos.trailing_stop = new_trail
        else:
            profit = pos.entry_price - candle_close
            if profit >= atr * self.trailing_activation_atr:
                new_trail = candle_close + (current_atr * self.atr_multiplier)
                if new_trail < pos.trailing_stop:
                    pos.trailing_stop = new_trail

    def _check_trailing_stop(self, pos: Position, candle_low: float, candle_high: float) -> bool:
        if pos.direction == "long" and candle_low <= pos.trailing_stop and pos.trailing_stop > pos.stop_loss:
            pos.state = PositionState.CLOSED_TRAIL
            logger.info("Trailing stop hit for %s %s at %.4f", pos.direction, pos.pair, pos.trailing_stop)
            return True
        if pos.direction == "short" and candle_high >= pos.trailing_stop and pos.trailing_stop < pos.stop_loss:
            pos.state = PositionState.CLOSED_TRAIL
            logger.info("Trailing stop hit for %s %s at %.4f", pos.direction, pos.pair, pos.trailing_stop)
            return True
        return False
