"""Orchestrator that calculates all indicators in a single pass."""
from __future__ import annotations

import logging

import pandas as pd

from config import settings
from indicators.rsi import calc_rsi
from indicators.mfi import calc_mfi
from indicators.tsi import calc_tsi
from indicators.atr import calc_atr
from indicators.ema import calc_ema

logger = logging.getLogger(__name__)


def calculate_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate all technical indicators and add them as columns.

    Input:  DataFrame with columns [timestamp, open, high, low, close, volume]
    Output: DataFrame with original columns + indicator columns added
    """
    df = df.copy()

    df["rsi"] = calc_rsi(df["close"], period=settings.RSI_PERIOD)
    df["mfi"] = calc_mfi(df, period=settings.MFI_PERIOD)
    df["tsi"] = calc_tsi(df["close"], long_period=settings.TSI_LONG_PERIOD, short_period=settings.TSI_SHORT_PERIOD)
    df["tsi_signal"] = calc_ema(df["tsi"], period=settings.TSI_SIGNAL_PERIOD)
    df["atr"] = calc_atr(df, period=settings.ATR_PERIOD)
    df["ema_20"] = calc_ema(df["close"], period=settings.EMA_SHORT)
    df["ema_50"] = calc_ema(df["close"], period=settings.EMA_MID)
    df["ema_200"] = calc_ema(df["close"], period=settings.EMA_LONG)

    logger.info(
        "Indicators calculated: %d rows, NaN in first %d rows (warmup)",
        len(df), settings.WARMUP_CANDLES,
    )

    return df
