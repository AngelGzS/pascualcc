"""Tests for risk management (position sizing, kill switch, portfolio)."""
from __future__ import annotations

import pytest

from config.types import Position, PositionState, ScoredSignal, Signal
from risk.position_sizer import PositionSizer
from risk.kill_switch import KillSwitch
from risk.portfolio import PortfolioManager


def _make_position(pair: str = "BTCUSDT", direction: str = "long", state: PositionState = PositionState.OPEN) -> Position:
    sig = Signal(
        timestamp=0, pair=pair, direction=direction,
        signal_type="regular_bullish", divergence_indicators=["rsi"],
        bos_confirmed=False, trend_context="neutral", ema_alignment="partial",
        price_at_signal=60000.0, atr_at_signal=150.0,
        rsi_value=35.0, mfi_value=30.0, tsi_value=-10.0,
    )
    scored = ScoredSignal(signal=sig, confluence_score=60, should_trade=True)
    return Position(pair=pair, direction=direction, state=state, signal=scored)


class TestPositionSizer:
    def test_basic_sizing(self) -> None:
        sizer = PositionSizer(risk_per_trade=0.02, atr_multiplier=2.0)
        size = sizer.calculate_size(capital=500.0, atr=150.0, current_price=60000.0)

        # risk = 500 * 0.02 = 10
        # stop_dist = 150 * 2 = 300
        # Uncapped size = 10 / 300 = 0.03333
        # But position value = 0.03333 * 60000 = $2000 > $500 capital
        # So capped at capital / price = 500 / 60000 = 0.008333
        assert abs(size - 500.0 / 60000.0) < 1e-6

    def test_capped_by_spot_capital(self) -> None:
        sizer = PositionSizer(risk_per_trade=0.02, atr_multiplier=2.0)
        # Very small ATR -> large position -> should be capped
        size = sizer.calculate_size(capital=500.0, atr=0.01, current_price=60000.0)
        max_size = 500.0 / 60000.0
        assert size <= max_size + 1e-10

    def test_zero_atr(self) -> None:
        sizer = PositionSizer()
        size = sizer.calculate_size(capital=500.0, atr=0.0, current_price=60000.0)
        assert size == 0.0

    def test_risk_calculation(self) -> None:
        sizer = PositionSizer(atr_multiplier=2.0)
        risk = sizer.calculate_risk_usd(size=0.01, atr=150.0)
        assert abs(risk - 0.01 * 150.0 * 2.0) < 1e-6


class TestKillSwitch:
    def test_no_kill_on_small_drawdown(self) -> None:
        ks = KillSwitch(max_drawdown=0.15, max_daily_loss=1.0)  # Disable daily loss for this test
        ks.initialize(500.0)
        assert not ks.update(450.0)  # 10% drawdown, under threshold

    def test_kill_on_threshold(self) -> None:
        ks = KillSwitch(max_drawdown=0.15)
        ks.initialize(500.0)
        assert ks.update(425.0)  # 15% drawdown
        assert ks.is_killed

    def test_kill_remains_killed(self) -> None:
        ks = KillSwitch(max_drawdown=0.15)
        ks.initialize(500.0)
        ks.update(425.0)
        assert ks.update(500.0)  # Even if equity recovers

    def test_peak_updates(self) -> None:
        ks = KillSwitch(max_drawdown=0.15)
        ks.initialize(500.0)
        ks.update(600.0)  # New peak
        assert ks.peak_equity == 600.0
        # 15% of 600 = 90, so kill at 510
        assert not ks.update(520.0)
        assert ks.update(510.0)

    def test_daily_loss_kill(self) -> None:
        ks = KillSwitch(max_drawdown=0.15, max_daily_loss=0.05)
        ks.initialize(500.0)
        ks.reset_daily(500.0)
        assert ks.update(475.0)  # 5% daily loss

    def test_drawdown_calculation(self) -> None:
        ks = KillSwitch()
        ks.initialize(500.0)
        ks.update(600.0)
        dd = ks.get_current_drawdown(540.0)
        assert abs(dd - 0.1) < 1e-10  # 60/600 = 10%


class TestPortfolioManager:
    def test_can_open_first_position(self) -> None:
        pm = PortfolioManager(max_positions=3)
        allowed, reason = pm.can_open_position("BTCUSDT", "long", [], 500.0, 10.0)
        assert allowed

    def test_max_positions_enforced(self) -> None:
        pm = PortfolioManager(max_positions=2)
        positions = [
            _make_position("BTCUSDT", "long"),
            _make_position("ETHUSDT", "long"),
        ]
        allowed, reason = pm.can_open_position("SOLUSDT", "long", positions, 500.0, 10.0)
        assert not allowed
        assert "Max positions" in reason

    def test_duplicate_pair_blocked(self) -> None:
        pm = PortfolioManager(max_positions=3)
        positions = [_make_position("BTCUSDT", "long")]
        allowed, reason = pm.can_open_position("BTCUSDT", "short", positions, 500.0, 10.0)
        assert not allowed
        assert "Already have" in reason

    def test_altcoin_correlation_limit(self) -> None:
        pm = PortfolioManager(max_positions=5, max_altcoin_same_dir=2)
        positions = [
            _make_position("SOLUSDT", "long"),
            _make_position("BNBUSDT", "long"),
        ]
        # Third altcoin long should be blocked
        allowed, reason = pm.can_open_position("XRPUSDT", "long", positions, 500.0, 10.0)
        assert not allowed

    def test_independent_pairs_not_correlated(self) -> None:
        pm = PortfolioManager(max_positions=5, max_altcoin_same_dir=2, max_directional_exposure=0.10)
        positions = [
            _make_position("SOLUSDT", "long"),
            _make_position("BNBUSDT", "long"),
        ]
        # BTC is independent, should be allowed (altcoin correlation doesn't apply)
        allowed, reason = pm.can_open_position("BTCUSDT", "long", positions, 500.0, 10.0)
        assert allowed

    def test_exposure_summary(self) -> None:
        pm = PortfolioManager()
        positions = [
            _make_position("BTCUSDT", "long"),
            _make_position("ETHUSDT", "short"),
            _make_position("SOLUSDT", "long", PositionState.PENDING_ENTRY),
        ]
        summary = pm.get_exposure_summary(positions)
        assert summary["total_positions"] == 3
        assert summary["long_positions"] == 2
        assert summary["short_positions"] == 1
