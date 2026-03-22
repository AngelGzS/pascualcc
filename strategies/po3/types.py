"""PO3 (Power of 3) strategy data types."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FVG:
    """Fair Value Gap: 3-candle price imbalance."""

    timestamp: int
    direction: str          # 'bullish' | 'bearish'
    top: float              # upper boundary
    bottom: float           # lower boundary
    midpoint: float         # (top + bottom) / 2
    timeframe: str          # '4h', '15m', etc.
    bar_index: int = 0
    filled: bool = False    # True once price returns through it


@dataclass
class LiquiditySweep:
    """A candle that swept a prior swing high/low then reversed."""

    timestamp: int
    sweep_type: str         # 'high_sweep' | 'low_sweep'
    level_swept: float      # the high/low that was taken
    sweep_candle_high: float
    sweep_candle_low: float
    bar_index: int = 0


@dataclass
class CISD:
    """Change in State of Delivery: market structure shift + displacement + FVG."""

    timestamp: int
    direction: str          # 'bullish' | 'bearish'
    displacement_index: int # bar index of the displacement candle
    fvg: FVG               # FVG formed by the displacement
    bar_index: int = 0


@dataclass
class PO3Signal:
    """Complete PO3 trade signal with entry, SL, and TP levels."""

    timestamp: int
    symbol: str
    direction: str              # 'long' | 'short'
    bias_type: str              # 'high_sweep_short' | 'low_sweep_long'

    # Context
    htf_fvg: FVG               # higher-timeframe FVG that validated the bias
    entry_fvg: FVG             # 15m FVG for limit entry
    sweep: LiquiditySweep      # the manipulation sweep
    cisd: CISD                 # the structure shift

    # Trade levels
    entry_price: float          # midpoint of entry FVG
    stop_loss: float            # edge of entry FVG (above for short, below for long)
    take_profit_2r: float       # 1:2 R:R target
    take_profit_3r: float       # 1:3 R:R target
    risk_points: float          # |entry - SL|


@dataclass
class PO3TradeRecord:
    """Closed PO3 trade with result."""

    signal: PO3Signal
    entry_fill_price: float
    exit_price: float
    exit_reason: str            # 'tp2r', 'tp3r', 'stop_loss', 'timeout'
    pnl_usd: float
    pnl_percent: float
    duration_minutes: int
    entry_time: int
    exit_time: int
