"""Position sizing based on ATR and fixed percentage risk."""
from __future__ import annotations

import logging

from config import settings

logger = logging.getLogger(__name__)


class PositionSizer:
    """Calculates position size ensuring max 2% risk per trade."""

    def __init__(
        self,
        risk_per_trade: float = settings.RISK_PER_TRADE,
        atr_multiplier: float = settings.ATR_MULTIPLIER,
        max_leverage: float = settings.MAX_LEVERAGE,
    ) -> None:
        self.risk_per_trade = risk_per_trade
        self.atr_multiplier = atr_multiplier
        self.max_leverage = max_leverage

    def calculate_size(
        self,
        capital: float,
        atr: float,
        current_price: float,
        is_futures: bool = False,
    ) -> float:
        """Calculate position size in base asset units.

        Formula:
            risk_amount = capital * risk_per_trade
            stop_distance = atr * atr_multiplier
            size = risk_amount / stop_distance

        For spot, cap at available capital. For futures, cap at max leverage.
        """
        risk_amount = capital * self.risk_per_trade
        stop_distance = atr * self.atr_multiplier

        if stop_distance <= 0:
            logger.warning("Invalid stop distance (ATR=%.6f), returning 0", atr)
            return 0.0

        size = risk_amount / stop_distance

        # Value in quote currency
        position_value = size * current_price

        if is_futures:
            max_value = capital * self.max_leverage
            if position_value > max_value:
                size = max_value / current_price
                logger.info(
                    "Position capped by leverage: %.6f (max value %.2f)",
                    size, max_value,
                )
        else:
            # Spot: can't exceed available capital
            if position_value > capital:
                size = capital / current_price
                logger.info(
                    "Position capped by capital: %.6f (capital %.2f)",
                    size, capital,
                )

        logger.debug(
            "Position size: %.6f (risk=$%.2f, stop_dist=%.4f, value=$%.2f)",
            size, risk_amount, stop_distance, size * current_price,
        )
        return size

    def calculate_risk_usd(self, size: float, atr: float) -> float:
        """Calculate the USD risk for a given position size."""
        return size * atr * self.atr_multiplier
