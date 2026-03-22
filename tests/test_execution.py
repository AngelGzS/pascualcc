"""Tests for execution (entry trailing, exit management)."""
from __future__ import annotations

import pytest

from config.types import Position, PositionState, ScoredSignal, Signal
from execution.entry import EntryTrailer
from execution.exit import ExitManager


def _make_scored_signal(direction: str = "long") -> ScoredSignal:
    sig = Signal(
        timestamp=0, pair="BTCUSDT", direction=direction,
        signal_type="regular_bullish" if direction == "long" else "regular_bearish",
        divergence_indicators=["rsi"], bos_confirmed=True,
        trend_context="bullish" if direction == "long" else "bearish",
        ema_alignment="aligned", price_at_signal=60000.0,
        atr_at_signal=150.0, rsi_value=35.0, mfi_value=30.0, tsi_value=-10.0,
    )
    return ScoredSignal(signal=sig, confluence_score=70, should_trade=True, confidence="strong")


class TestEntryTrailer:
    def test_create_pending_long(self) -> None:
        trailer = EntryTrailer(entry_factor=0.5, entry_timeout=8)
        scored = _make_scored_signal("long")
        pos = trailer.create_pending_position(scored, current_price=60000.0, atr=150.0)

        assert pos.state == PositionState.PENDING_ENTRY
        assert pos.direction == "long"
        assert pos.entry_trigger == 60000.0 - (150.0 * 0.5)
        assert pos.entry_timeout_remaining == 8

    def test_create_pending_short(self) -> None:
        trailer = EntryTrailer(entry_factor=0.5, entry_timeout=8)
        scored = _make_scored_signal("short")
        pos = trailer.create_pending_position(scored, current_price=60000.0, atr=150.0)

        assert pos.entry_trigger == 60000.0 + (150.0 * 0.5)

    def test_trailing_up_for_long(self) -> None:
        trailer = EntryTrailer(entry_factor=0.5, entry_timeout=8)
        scored = _make_scored_signal("long")
        pos = trailer.create_pending_position(scored, current_price=60000.0, atr=150.0)
        old_trigger = pos.entry_trigger

        # Price moves up -> trigger should trail up
        pos = trailer.update_trailing(pos, current_price=61000.0, atr=150.0)
        assert pos.entry_trigger > old_trigger

    def test_trigger_never_drops_for_long(self) -> None:
        trailer = EntryTrailer(entry_factor=0.5, entry_timeout=8)
        scored = _make_scored_signal("long")
        pos = trailer.create_pending_position(scored, current_price=60000.0, atr=150.0)

        # Trail up
        pos = trailer.update_trailing(pos, current_price=61000.0, atr=150.0)
        high_trigger = pos.entry_trigger

        # Price drops -> trigger should NOT drop
        pos = trailer.update_trailing(pos, current_price=59000.0, atr=150.0)
        assert pos.entry_trigger >= high_trigger

    def test_timeout_cancels(self) -> None:
        trailer = EntryTrailer(entry_factor=0.5, entry_timeout=2)
        scored = _make_scored_signal("long")
        pos = trailer.create_pending_position(scored, current_price=60000.0, atr=150.0)

        pos = trailer.update_trailing(pos, current_price=60000.0, atr=150.0)
        assert pos.state == PositionState.PENDING_ENTRY

        pos = trailer.update_trailing(pos, current_price=60000.0, atr=150.0)
        assert pos.state == PositionState.CANCELLED

    def test_check_entry_long(self) -> None:
        trailer = EntryTrailer(entry_factor=0.5, entry_timeout=8)
        scored = _make_scored_signal("long")
        pos = trailer.create_pending_position(scored, current_price=60000.0, atr=150.0)
        # trigger = 60000 - 75 = 59925

        # Price doesn't reach trigger
        assert not trailer.check_entry(pos, candle_low=59950.0, candle_high=60100.0)

        # Price reaches trigger
        assert trailer.check_entry(pos, candle_low=59900.0, candle_high=60100.0)


class TestExitManager:
    def _make_long_position(self) -> Position:
        scored = _make_scored_signal("long")
        pos = Position(
            pair="BTCUSDT", direction="long", state=PositionState.OPEN,
            signal=scored, entry_price=60000.0, size=0.01,
            original_size=0.01, atr_at_entry=150.0,
        )
        exit_mgr = ExitManager(atr_multiplier=2.0, tp_multiplier=3.0)
        return exit_mgr.initialize_exit_levels(pos)

    def test_stop_loss_levels(self) -> None:
        pos = self._make_long_position()
        # SL = 60000 - 150*2 = 59700
        assert abs(pos.stop_loss - 59700.0) < 0.01
        # TP = 60000 + 150*3 = 60450
        assert abs(pos.take_profit - 60450.0) < 0.01

    def test_stop_loss_hit(self) -> None:
        pos = self._make_long_position()
        exit_mgr = ExitManager(atr_multiplier=2.0, tp_multiplier=3.0)

        pos = exit_mgr.check_exits(pos, candle_high=60100.0, candle_low=59600.0, candle_close=59650.0, current_atr=150.0)
        assert pos.state == PositionState.CLOSED_SL

    def test_take_profit_hit(self) -> None:
        pos = self._make_long_position()
        # Disable partial TP to test full TP directly
        exit_mgr = ExitManager(atr_multiplier=2.0, tp_multiplier=3.0, partial_tp_atr=999.0)

        pos = exit_mgr.check_exits(pos, candle_high=60500.0, candle_low=60000.0, candle_close=60450.0, current_atr=150.0)
        assert pos.state == PositionState.CLOSED_TP

    def test_position_survives_normal_candle(self) -> None:
        pos = self._make_long_position()
        exit_mgr = ExitManager(atr_multiplier=2.0, tp_multiplier=3.0)

        pos = exit_mgr.check_exits(pos, candle_high=60200.0, candle_low=59800.0, candle_close=60100.0, current_atr=150.0)
        assert pos.state == PositionState.OPEN

    def test_excursion_tracking(self) -> None:
        pos = self._make_long_position()
        exit_mgr = ExitManager(atr_multiplier=2.0, tp_multiplier=3.0)

        pos = exit_mgr.check_exits(pos, candle_high=60300.0, candle_low=59800.0, candle_close=60100.0, current_atr=150.0)
        assert pos.max_favorable == 300.0  # 60300 - 60000
        assert pos.max_adverse == 200.0    # 60000 - 59800
