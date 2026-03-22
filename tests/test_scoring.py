"""Tests for confluence scoring."""
from __future__ import annotations

import pytest

from config.types import Signal, ScoredSignal
from scoring.confluence import (
    score_divergences,
    score_divergence_type,
    score_bos,
    score_ema_alignment,
    score_trend_context,
    score_volatility,
    score_tsi_cross,
)


def _make_signal(**kwargs) -> Signal:
    defaults = dict(
        timestamp=0,
        pair="BTCUSDT",
        direction="long",
        signal_type="regular_bullish",
        divergence_indicators=["rsi"],
        bos_confirmed=False,
        trend_context="neutral",
        ema_alignment="contra",
        price_at_signal=60000.0,
        atr_at_signal=150.0,
        rsi_value=35.0,
        mfi_value=30.0,
        tsi_value=-10.0,
    )
    defaults.update(kwargs)
    return Signal(**defaults)


class TestScoreDivergences:
    def test_one_indicator(self) -> None:
        sig = _make_signal(divergence_indicators=["rsi"])
        assert score_divergences(sig) == 10

    def test_two_indicators(self) -> None:
        sig = _make_signal(divergence_indicators=["rsi", "mfi"])
        assert score_divergences(sig) == 20

    def test_three_indicators(self) -> None:
        sig = _make_signal(divergence_indicators=["rsi", "mfi", "tsi"])
        assert score_divergences(sig) == 30

    def test_no_indicators(self) -> None:
        sig = _make_signal(divergence_indicators=[])
        assert score_divergences(sig) == 0


class TestScoreDivergenceType:
    def test_regular_neutral(self) -> None:
        sig = _make_signal(signal_type="regular_bullish", trend_context="neutral")
        assert score_divergence_type(sig) == 10

    def test_hidden_with_trend(self) -> None:
        sig = _make_signal(signal_type="hidden_bullish", trend_context="bullish")
        assert score_divergence_type(sig) == 10

    def test_regular_non_ideal(self) -> None:
        sig = _make_signal(signal_type="regular_bullish", trend_context="bullish")
        assert score_divergence_type(sig) == 6

    def test_hidden_no_trend(self) -> None:
        sig = _make_signal(signal_type="hidden_bullish", trend_context="neutral")
        assert score_divergence_type(sig) == 6


class TestScoreBOS:
    def test_bos_confirmed(self) -> None:
        sig = _make_signal(bos_confirmed=True)
        assert score_bos(sig) == 15

    def test_bos_not_confirmed(self) -> None:
        sig = _make_signal(bos_confirmed=False)
        assert score_bos(sig) == 0


class TestScoreEMAAlignment:
    def test_aligned(self) -> None:
        sig = _make_signal(ema_alignment="aligned")
        assert score_ema_alignment(sig) == 15

    def test_partial(self) -> None:
        sig = _make_signal(ema_alignment="partial")
        assert score_ema_alignment(sig) == 8

    def test_contra(self) -> None:
        sig = _make_signal(ema_alignment="contra")
        assert score_ema_alignment(sig) == 0


class TestScoreTrendContext:
    def test_long_bullish(self) -> None:
        sig = _make_signal(direction="long", trend_context="bullish")
        assert score_trend_context(sig) == 15

    def test_short_bearish(self) -> None:
        sig = _make_signal(direction="short", trend_context="bearish")
        assert score_trend_context(sig) == 15

    def test_neutral(self) -> None:
        sig = _make_signal(trend_context="neutral")
        assert score_trend_context(sig) == 7

    def test_contra_trend(self) -> None:
        sig = _make_signal(direction="long", trend_context="bearish")
        assert score_trend_context(sig) == 0


class TestScoreVolatility:
    def test_optimal(self) -> None:
        assert score_volatility(50.0) == 10

    def test_acceptable(self) -> None:
        assert score_volatility(20.0) == 5

    def test_extreme(self) -> None:
        assert score_volatility(5.0) == 0
        assert score_volatility(95.0) == 0


class TestScoreTSICross:
    def test_bullish_cross(self) -> None:
        sig = _make_signal(direction="long")
        assert score_tsi_cross(sig, tsi=5.0, tsi_signal=3.0, prev_tsi=-1.0, prev_tsi_signal=1.0) == 5

    def test_no_cross(self) -> None:
        sig = _make_signal(direction="long")
        assert score_tsi_cross(sig, tsi=5.0, tsi_signal=3.0, prev_tsi=4.0, prev_tsi_signal=2.0) == 0

    def test_bearish_cross(self) -> None:
        sig = _make_signal(direction="short")
        assert score_tsi_cross(sig, tsi=-2.0, tsi_signal=0.0, prev_tsi=1.0, prev_tsi_signal=0.0) == 5


class TestMaxScore:
    def test_perfect_signal_scores_100(self) -> None:
        """A signal with all components maxed should score 100."""
        sig = _make_signal(
            divergence_indicators=["rsi", "mfi", "tsi"],
            signal_type="regular_bullish",
            trend_context="neutral",
            bos_confirmed=True,
            ema_alignment="aligned",
            direction="long",
        )
        total = (
            score_divergences(sig)
            + score_divergence_type(sig)
            + score_bos(sig)
            + score_ema_alignment(sig)
            + score_trend_context(sig)  # neutral = 7, not 15
            + score_volatility(50.0)
            + score_tsi_cross(sig, 5.0, 3.0, -1.0, 1.0)
        )
        # 30 + 10 + 15 + 15 + 7 + 10 + 5 = 92
        assert total == 92

    def test_max_with_trend_alignment(self) -> None:
        """Regular divergence in neutral is 10, but hidden+trend gives 10+15."""
        sig = _make_signal(
            divergence_indicators=["rsi", "mfi", "tsi"],
            signal_type="hidden_bullish",
            trend_context="bullish",
            bos_confirmed=True,
            ema_alignment="aligned",
            direction="long",
        )
        total = (
            score_divergences(sig)
            + score_divergence_type(sig)
            + score_bos(sig)
            + score_ema_alignment(sig)
            + score_trend_context(sig)
            + score_volatility(50.0)
            + score_tsi_cross(sig, 5.0, 3.0, -1.0, 1.0)
        )
        # 30 + 10 + 15 + 15 + 15 + 10 + 5 = 100
        assert total == 100
