"""Kill switch: emergency stop when drawdown exceeds threshold."""
from __future__ import annotations

import logging

from config import settings

logger = logging.getLogger(__name__)


class KillSwitch:
    """Monitors portfolio drawdown and triggers emergency shutdown."""

    def __init__(
        self,
        max_drawdown: float = settings.KILL_SWITCH_DRAWDOWN,
        max_daily_loss: float = settings.KILL_SWITCH_DAILY_LOSS,
    ) -> None:
        self.max_drawdown = max_drawdown
        self.max_daily_loss = max_daily_loss
        self.peak_equity: float = 0.0
        self.daily_start_equity: float = 0.0
        self.is_killed: bool = False
        self._kill_reason: str = ""

    def initialize(self, equity: float) -> None:
        """Initialize with starting equity."""
        self.peak_equity = equity
        self.daily_start_equity = equity
        self.is_killed = False
        self._kill_reason = ""

    def reset_daily(self, equity: float) -> None:
        """Reset daily tracking at start of new day."""
        self.daily_start_equity = equity

    def update(self, current_equity: float) -> bool:
        """Update and check kill conditions.

        Returns True if kill switch is activated.
        """
        if self.is_killed:
            return True

        # Update peak
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        # Check total drawdown from peak
        if self.peak_equity > 0:
            drawdown = (self.peak_equity - current_equity) / self.peak_equity
            if drawdown >= self.max_drawdown:
                self.is_killed = True
                self._kill_reason = (
                    f"Total drawdown {drawdown:.1%} >= {self.max_drawdown:.1%} "
                    f"(peak=${self.peak_equity:.2f}, current=${current_equity:.2f})"
                )
                logger.critical("KILL SWITCH ACTIVATED: %s", self._kill_reason)
                return True

        # Check daily loss
        if self.daily_start_equity > 0:
            daily_loss = (self.daily_start_equity - current_equity) / self.daily_start_equity
            if daily_loss >= self.max_daily_loss:
                self.is_killed = True
                self._kill_reason = (
                    f"Daily loss {daily_loss:.1%} >= {self.max_daily_loss:.1%} "
                    f"(day_start=${self.daily_start_equity:.2f}, current=${current_equity:.2f})"
                )
                logger.critical("KILL SWITCH ACTIVATED: %s", self._kill_reason)
                return True

        return False

    @property
    def kill_reason(self) -> str:
        return self._kill_reason

    def get_current_drawdown(self, current_equity: float) -> float:
        """Calculate current drawdown from peak."""
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - current_equity) / self.peak_equity
