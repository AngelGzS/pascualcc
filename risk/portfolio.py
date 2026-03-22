"""Portfolio exposure management: max positions, correlation, directional limits."""
from __future__ import annotations

import logging

from config import settings
from config.types import Position, PositionState

logger = logging.getLogger(__name__)


class PortfolioManager:
    """Controls overall portfolio exposure and position limits."""

    def __init__(
        self,
        max_positions: int = settings.MAX_OPEN_POSITIONS,
        max_total_risk: float = settings.MAX_TOTAL_RISK,
        max_altcoin_same_dir: int = settings.MAX_ALTCOIN_SAME_DIR,
        max_directional_exposure: float = settings.MAX_DIRECTIONAL_EXPOSURE,
        independent_pairs: list[str] | None = None,
    ) -> None:
        self.max_positions = max_positions
        self.max_total_risk = max_total_risk
        self.max_altcoin_same_dir = max_altcoin_same_dir
        self.max_directional_exposure = max_directional_exposure
        self.independent_pairs = independent_pairs or settings.INDEPENDENT_PAIRS

    def _active_positions(self, positions: list[Position]) -> list[Position]:
        """Filter to only active (open or partial TP) positions."""
        active_states = {PositionState.OPEN, PositionState.PARTIAL_TP, PositionState.PENDING_ENTRY}
        return [p for p in positions if p.state in active_states]

    def can_open_position(
        self,
        new_pair: str,
        new_direction: str,
        positions: list[Position],
        capital: float,
        risk_per_trade_usd: float,
    ) -> tuple[bool, str]:
        """Check if a new position can be opened given current exposure.

        Returns (allowed, reason).
        """
        active = self._active_positions(positions)

        # Max positions check
        if len(active) >= self.max_positions:
            return False, f"Max positions reached ({self.max_positions})"

        # Max total risk check
        total_risk = len(active) * settings.RISK_PER_TRADE
        if total_risk + settings.RISK_PER_TRADE > self.max_total_risk:
            return False, f"Total risk would exceed {self.max_total_risk:.0%}"

        # Correlation check: count same-direction altcoin positions
        if new_pair not in self.independent_pairs:
            same_dir_altcoins = sum(
                1 for p in active
                if p.pair not in self.independent_pairs
                and p.direction == new_direction
            )
            if same_dir_altcoins >= self.max_altcoin_same_dir:
                return False, f"Max altcoin {new_direction} positions reached ({self.max_altcoin_same_dir})"

        # Directional exposure check
        long_count = sum(1 for p in active if p.direction == "long")
        short_count = sum(1 for p in active if p.direction == "short")

        if new_direction == "long":
            long_count += 1
        else:
            short_count += 1

        net_exposure = abs(long_count - short_count) * settings.RISK_PER_TRADE
        if net_exposure > self.max_directional_exposure:
            return False, f"Directional exposure would exceed {self.max_directional_exposure:.0%}"

        # Duplicate pair check
        for p in active:
            if p.pair == new_pair:
                return False, f"Already have position in {new_pair}"

        return True, "OK"

    def get_exposure_summary(self, positions: list[Position]) -> dict[str, int]:
        """Get a summary of current exposure."""
        active = self._active_positions(positions)
        return {
            "total_positions": len(active),
            "long_positions": sum(1 for p in active if p.direction == "long"),
            "short_positions": sum(1 for p in active if p.direction == "short"),
            "pending_entries": sum(1 for p in active if p.state == PositionState.PENDING_ENTRY),
        }
