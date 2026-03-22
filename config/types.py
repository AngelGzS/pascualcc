"""Shared dataclasses and types used across the system."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass
class Candle:
    timestamp: int       # Unix ms of open
    open: float
    high: float
    low: float
    close: float
    volume: float        # Volume in base asset
    quote_volume: float  # Volume in quote asset (USDT)
    trades: int          # Number of trades in the candle
    close_time: int      # Unix ms of close


@dataclass
class Signal:
    timestamp: int
    pair: str
    direction: str              # 'long' | 'short'
    signal_type: str            # 'regular_bullish' | 'regular_bearish' | 'hidden_bullish' | 'hidden_bearish'
    divergence_indicators: list[str]  # e.g. ['rsi', 'mfi']
    bos_confirmed: bool
    trend_context: str          # 'bullish' | 'bearish' | 'neutral'
    ema_alignment: str          # 'aligned' | 'partial' | 'contra'
    price_at_signal: float
    atr_at_signal: float
    rsi_value: float
    mfi_value: float
    tsi_value: float


@dataclass
class ScoredSignal:
    signal: Signal
    confluence_score: int       # 0-100
    score_breakdown: dict[str, int] = field(default_factory=dict)
    should_trade: bool = False
    confidence: str = "weak"    # 'weak' | 'moderate' | 'strong'


class PositionState(Enum):
    PENDING_ENTRY = "pending_entry"
    OPEN = "open"
    PARTIAL_TP = "partial_tp"
    CLOSED_SL = "closed_sl"
    CLOSED_TP = "closed_tp"
    CLOSED_TRAIL = "closed_trail"
    CANCELLED = "cancelled"
    KILLED = "killed"


@dataclass
class TradeRecord:
    trade_id: str
    pair: str
    direction: str
    confluence_score: int
    entry_price: float
    entry_time: int
    exit_price: float
    exit_time: int
    exit_reason: str            # 'stop_loss' | 'take_profit' | 'trailing' | 'kill_switch'
    position_size: float
    pnl_usd: float
    pnl_percent: float
    atr_at_entry: float
    atr_multiplier: float
    max_favorable_excursion: float
    max_adverse_excursion: float
    duration_candles: int


@dataclass
class Position:
    pair: str
    direction: str              # 'long' | 'short'
    state: PositionState
    signal: ScoredSignal
    entry_price: float = 0.0
    entry_time: int = 0
    size: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    trailing_stop: float = 0.0
    partial_tp_done: bool = False
    original_size: float = 0.0
    entry_trigger: float = 0.0
    entry_timeout_remaining: int = 0
    max_favorable: float = 0.0
    max_adverse: float = 0.0
    candles_in_trade: int = 0
    atr_at_entry: float = 0.0
