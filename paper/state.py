"""Persistence for paper trading state — save/load to JSON."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from typing import Any

from config import settings
from config.types import (
    Position, PositionState, ScoredSignal, Signal, TradeRecord,
)

logger = logging.getLogger(__name__)


def _state_path(pair: str, timeframe: str) -> str:
    """Return the file path for a pair/timeframe state file."""
    os.makedirs(settings.PAPER_STATE_DIR, exist_ok=True)
    return os.path.join(settings.PAPER_STATE_DIR, f"{pair}_{timeframe}_state.json")


def save_state(
    pair: str,
    timeframe: str,
    capital: float,
    initial_capital: float,
    positions: list[Position],
    trades: list[TradeRecord],
    equity_curve: list[dict[str, Any]],
    start_time: int,
    candles_processed: int,
) -> None:
    """Persist current paper trading state to JSON."""
    state = {
        "pair": pair,
        "timeframe": timeframe,
        "capital": capital,
        "initial_capital": initial_capital,
        "start_time": start_time,
        "candles_processed": candles_processed,
        "positions": [_position_to_dict(p) for p in positions],
        "trades": [asdict(t) for t in trades],
        "equity_curve": equity_curve,
    }

    path = _state_path(pair, timeframe)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, path)  # Atomic on most OS
        logger.debug("State saved: %s", path)
    except Exception as e:
        logger.error("Failed to save state: %s", e)


def load_state(pair: str, timeframe: str) -> dict[str, Any] | None:
    """Load paper trading state from JSON. Returns None if no state file."""
    path = _state_path(pair, timeframe)
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info("State loaded from %s", path)
        return {
            "capital": data["capital"],
            "initial_capital": data["initial_capital"],
            "start_time": data["start_time"],
            "candles_processed": data["candles_processed"],
            "trades": [_dict_to_trade(t) for t in data.get("trades", [])],
            "positions": [_dict_to_position(p) for p in data.get("positions", [])],
            "equity_curve": data.get("equity_curve", []),
        }
    except Exception as e:
        logger.error("Failed to load state from %s: %s", path, e)
        return None


def _position_to_dict(pos: Position) -> dict[str, Any]:
    """Serialize a Position to a JSON-friendly dict."""
    d = asdict(pos)
    d["state"] = pos.state.value
    # ScoredSignal → nested dict (already handled by asdict)
    return d


def _dict_to_trade(d: dict[str, Any]) -> TradeRecord:
    """Deserialize a dict to TradeRecord."""
    return TradeRecord(**d)


def _dict_to_position(d: dict[str, Any]) -> Position:
    """Deserialize a dict to Position (with nested Signal/ScoredSignal)."""
    d["state"] = PositionState(d["state"])

    # Reconstruct nested signal objects
    sig_data = d.pop("signal")
    inner_sig_data = sig_data.pop("signal")
    inner_signal = Signal(**inner_sig_data)
    scored_signal = ScoredSignal(signal=inner_signal, **sig_data)
    d["signal"] = scored_signal

    return Position(**d)
