"""Parse Telegram signal messages into structured data."""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Known coin suffixes to build full Binance pair
QUOTE_ASSET = "USDT"


@dataclass
class TelegramSignal:
    """Parsed trading signal from Telegram."""
    pair: str                    # "LINKUSDT"
    direction: str               # "long" | "short"
    leverage: int                # 10
    entry_type: str              # "market" | limit price as string
    targets: list[float] = field(default_factory=list)   # [8.835, 8.676, ...]
    stop_loss: float = 0.0
    timestamp: int = 0           # Unix ms from message date
    raw_text: str = ""


@dataclass
class ManagementUpdate:
    """Parsed management/update message (reply to a signal)."""
    action: str                  # "partial_close" | "move_sl" | "close_all" | "update"
    percentage: float = 0.0      # 0.25 for "Fix 25%"
    sl_to_breakeven: bool = False
    new_sl: float | None = None
    raw_text: str = ""


# ─── Regex patterns ────────────────────────────────────────────────────────

# Signal header: "LINK | SHORT 10x ⚡" or "BTC | LONG 20x ⚡"
_HEADER_RE = re.compile(
    r"([A-Za-z0-9]+)\s*\|\s*(LONG|SHORT)\s+(\d+)x",
    re.IGNORECASE,
)

# Entry: "Enter - market" or "Enter - 8.925" or "Entry - market"
_ENTRY_RE = re.compile(
    r"Enter?y?\s*[-–:]\s*(market|[\d.,]+)",
    re.IGNORECASE,
)

# Targets: "Target - 8.835 - 8.676 - 8.535 - 7.992"
# Also handles "Targets", "TP", "Take profit"
_TARGETS_RE = re.compile(
    r"(?:Target|Targets|TP|Take\s*profit)\s*[-–:]\s*([\d.,\s\-–]+)",
    re.IGNORECASE,
)

# Stop: "Stop - 9.373" or "SL - 9.373" or "Stoploss - 9.373"
_STOP_RE = re.compile(
    r"(?:Stop|SL|Stop\s*loss)\s*[-–:]\s*([\d.,]+)",
    re.IGNORECASE,
)

# Management: "Fix 25% here" or "Take 25% profit"
_PARTIAL_RE = re.compile(
    r"(?:Fix|Take|Close|Secure)\s+(\d+)%",
    re.IGNORECASE,
)

# SL to breakeven: "SL at breakeven" or "set up SL at breakeven" or "move SL to BE"
_BE_RE = re.compile(
    r"(?:SL|stop)\s+(?:at|to)\s+(?:breakeven|BE|entry)",
    re.IGNORECASE,
)

# Close all: "Close trade" or "Close position" or "Exit now"
_CLOSE_ALL_RE = re.compile(
    r"(?:Close|Exit|Cancel)\s+(?:trade|position|all|now)",
    re.IGNORECASE,
)

# New SL: "Move SL to 8.500" or "SL - 8.500"
_NEW_SL_RE = re.compile(
    r"(?:Move\s+)?(?:SL|stop)\s*(?:to|at|[-–:])\s*([\d.,]+)",
    re.IGNORECASE,
)


def _parse_number(s: str) -> float:
    """Parse a number string, handling commas."""
    return float(s.replace(",", "").strip())


def _extract_numbers(text: str) -> list[float]:
    """Extract all numbers from a string separated by - or spaces."""
    parts = re.split(r"[\s\-–]+", text.strip())
    numbers = []
    for p in parts:
        p = p.strip().rstrip(",").lstrip(",")
        if p:
            try:
                numbers.append(_parse_number(p))
            except ValueError:
                continue
    return numbers


def parse_signal(text: str, timestamp: int = 0) -> TelegramSignal | None:
    """Parse a Telegram message into a TelegramSignal.

    Returns None if the message doesn't match the signal format.
    """
    # Must have header
    header_match = _HEADER_RE.search(text)
    if not header_match:
        return None

    coin = header_match.group(1).upper()
    direction = header_match.group(2).lower()
    leverage = int(header_match.group(3))

    # Must have either targets or stop to be a valid signal
    targets_match = _TARGETS_RE.search(text)
    stop_match = _STOP_RE.search(text)

    if not targets_match and not stop_match:
        return None

    # Build pair
    pair = coin if coin.endswith(QUOTE_ASSET) else coin + QUOTE_ASSET

    # Entry type
    entry_match = _ENTRY_RE.search(text)
    entry_type = "market"
    if entry_match:
        val = entry_match.group(1).strip().lower()
        entry_type = "market" if val == "market" else val

    # Targets
    targets: list[float] = []
    if targets_match:
        targets = _extract_numbers(targets_match.group(1))

    # Stop loss
    stop_loss = 0.0
    if stop_match:
        try:
            stop_loss = _parse_number(stop_match.group(1))
        except ValueError:
            pass

    signal = TelegramSignal(
        pair=pair,
        direction=direction,
        leverage=leverage,
        entry_type=entry_type,
        targets=targets,
        stop_loss=stop_loss,
        timestamp=timestamp,
        raw_text=text,
    )

    logger.info(
        "Parsed signal: %s %s %dx, targets=%s, SL=%.4f",
        signal.direction.upper(), signal.pair, signal.leverage,
        signal.targets, signal.stop_loss,
    )
    return signal


def parse_management(text: str) -> ManagementUpdate | None:
    """Parse a management/update message.

    Returns None if the message doesn't match any management pattern.
    """
    # Check close all first
    if _CLOSE_ALL_RE.search(text):
        return ManagementUpdate(action="close_all", raw_text=text)

    # Check partial close
    partial_match = _PARTIAL_RE.search(text)
    be_match = _BE_RE.search(text)
    new_sl_match = _NEW_SL_RE.search(text)

    if not partial_match and not be_match and not new_sl_match:
        return None

    update = ManagementUpdate(action="update", raw_text=text)

    if partial_match:
        update.action = "partial_close"
        update.percentage = int(partial_match.group(1)) / 100.0

    if be_match:
        update.sl_to_breakeven = True

    if new_sl_match and not be_match:
        try:
            update.new_sl = _parse_number(new_sl_match.group(1))
        except ValueError:
            pass

    return update
