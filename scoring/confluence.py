"""Confluence scoring: combines signal components into a 0-100 score."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from config import settings
from config.types import Signal, ScoredSignal

logger = logging.getLogger(__name__)


def score_divergences(signal: Signal) -> int:
    """Score based on how many indicators confirm the divergence (max 30)."""
    n = len(signal.divergence_indicators)
    pts = 0
    if n >= 1:
        pts += 10
    if n >= 2:
        pts += 10
    if n >= 3:
        pts += 10
    return pts


def score_divergence_type(signal: Signal) -> int:
    """Score based on divergence type in context (max 10)."""
    if signal.signal_type.startswith("regular") and signal.trend_context == "neutral":
        return 10
    if signal.signal_type.startswith("hidden") and signal.trend_context != "neutral":
        return 10
    if signal.signal_type.startswith("regular"):
        return 6
    if signal.signal_type.startswith("hidden"):
        return 6
    return 0


def score_bos(signal: Signal) -> int:
    """Score based on BOS confirmation (max 15)."""
    return 15 if signal.bos_confirmed else 0


def score_ema_alignment(signal: Signal) -> int:
    """Score based on EMA alignment with signal direction (max 15)."""
    if signal.ema_alignment == "aligned":
        return 15
    if signal.ema_alignment == "partial":
        return 8
    return 0


def score_trend_context(signal: Signal) -> int:
    """Score based on trend context alignment (max 15)."""
    if signal.direction == "long" and signal.trend_context == "bullish":
        return 15
    if signal.direction == "short" and signal.trend_context == "bearish":
        return 15
    if signal.trend_context == "neutral":
        return 7
    return 0


def score_volatility(atr_percentile: float) -> int:
    """Score based on ATR percentile (max 10)."""
    if settings.VOLATILITY_OPTIMAL_LOW <= atr_percentile <= settings.VOLATILITY_OPTIMAL_HIGH:
        return 10
    if settings.VOLATILITY_ACCEPTABLE_LOW <= atr_percentile <= settings.VOLATILITY_ACCEPTABLE_HIGH:
        return 5
    return 0


def score_tsi_cross(
    signal: Signal,
    tsi: float,
    tsi_signal: float,
    prev_tsi: float,
    prev_tsi_signal: float,
) -> int:
    """Score based on TSI crossing its signal line (max 5)."""
    if signal.direction == "long":
        if prev_tsi <= prev_tsi_signal and tsi > tsi_signal:
            return 5
    if signal.direction == "short":
        if prev_tsi >= prev_tsi_signal and tsi < tsi_signal:
            return 5
    return 0


def calculate_confluence_score(
    signal: Signal,
    df: pd.DataFrame,
    bar_index: int,
    threshold: int = settings.CONFLUENCE_THRESHOLD,
) -> ScoredSignal:
    """Calculate the full confluence score for a signal.

    Args:
        signal: The raw Signal from the signal engine.
        df: DataFrame with indicators computed.
        bar_index: Index in df where the signal was generated.
        threshold: Minimum score to trade.

    Returns:
        ScoredSignal with score, breakdown, and trade decision.
    """
    # ATR percentile
    atr_val = signal.atr_at_signal
    valid_atr = df["atr"].dropna()
    if len(valid_atr) > 0:
        atr_pct = float((valid_atr < atr_val).sum() / len(valid_atr) * 100.0)
    else:
        atr_pct = 50.0

    # TSI cross
    tsi_val = df["tsi"].iloc[bar_index] if not pd.isna(df["tsi"].iloc[bar_index]) else 0.0
    tsi_sig = df["tsi_signal"].iloc[bar_index] if not pd.isna(df["tsi_signal"].iloc[bar_index]) else 0.0
    prev_tsi_val = 0.0
    prev_tsi_sig = 0.0
    if bar_index > 0:
        prev_tsi_val = df["tsi"].iloc[bar_index - 1] if not pd.isna(df["tsi"].iloc[bar_index - 1]) else 0.0
        prev_tsi_sig = df["tsi_signal"].iloc[bar_index - 1] if not pd.isna(df["tsi_signal"].iloc[bar_index - 1]) else 0.0

    breakdown = {
        "divergences": score_divergences(signal),
        "divergence_type": score_divergence_type(signal),
        "bos": score_bos(signal),
        "ema_alignment": score_ema_alignment(signal),
        "trend_context": score_trend_context(signal),
        "volatility": score_volatility(atr_pct),
        "tsi_cross": score_tsi_cross(signal, tsi_val, tsi_sig, prev_tsi_val, prev_tsi_sig),
    }

    total = sum(breakdown.values())

    if total >= 71:
        confidence = "strong"
    elif total >= 51:
        confidence = "moderate"
    else:
        confidence = "weak"

    should_trade = total >= threshold

    scored = ScoredSignal(
        signal=signal,
        confluence_score=total,
        score_breakdown=breakdown,
        should_trade=should_trade,
        confidence=confidence,
    )

    logger.debug(
        "Confluence score for %s %s: %d (%s) - trade: %s",
        signal.pair, signal.direction, total, confidence, should_trade,
    )

    return scored
