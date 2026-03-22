"""Core backtesting engine: simulates trading on historical data."""
from __future__ import annotations

import logging
import uuid

import pandas as pd

from config import settings
from config.types import Position, PositionState, ScoredSignal, TradeRecord, Signal
from indicators.calculator import calculate_all_indicators
from signals.engine import SignalEngine
from scoring.confluence import calculate_confluence_score
from execution.entry import EntryTrailer
from execution.exit import ExitManager
from risk.position_sizer import PositionSizer
from risk.kill_switch import KillSwitch
from risk.portfolio import PortfolioManager
from backtest.metrics import BacktestMetrics, calculate_metrics

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Event-driven backtesting engine that processes candles sequentially."""

    def __init__(
        self,
        initial_capital: float = settings.INITIAL_CAPITAL,
        commission: float = settings.COMMISSION_RATE,
        slippage: float = settings.SLIPPAGE_RATE,
        # Optimizable params
        atr_multiplier: float = settings.ATR_MULTIPLIER,
        confluence_threshold: int = settings.CONFLUENCE_THRESHOLD,
        entry_factor: float = settings.ENTRY_FACTOR,
        entry_timeout: int = settings.ENTRY_TIMEOUT,
        tp_multiplier: float = settings.TP_MULTIPLIER,
        pivot_left: int = settings.PIVOT_LEFT,
        pivot_right: int = settings.PIVOT_RIGHT,
    ) -> None:
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.atr_multiplier = atr_multiplier
        self.confluence_threshold = confluence_threshold

        self.signal_engine = SignalEngine(
            pivot_left=pivot_left,
            pivot_right=pivot_right,
        )
        self.entry_trailer = EntryTrailer(
            entry_factor=entry_factor,
            entry_timeout=entry_timeout,
        )
        self.exit_manager = ExitManager(
            atr_multiplier=atr_multiplier,
            tp_multiplier=tp_multiplier,
        )
        self.position_sizer = PositionSizer(
            atr_multiplier=atr_multiplier,
        )
        self.kill_switch = KillSwitch()
        self.portfolio_manager = PortfolioManager()

        self.positions: list[Position] = []
        self.trades: list[TradeRecord] = []
        self._current_day: int = -1

    def run(self, df: pd.DataFrame, pair: str) -> list[TradeRecord]:
        """Run backtest on a DataFrame with OHLCV data.

        The DataFrame should already have all indicator columns.
        """
        self.capital = self.initial_capital
        self.positions = []
        self.trades = []
        self.kill_switch.initialize(self.initial_capital)
        self._current_day = -1

        if len(df) < settings.WARMUP_CANDLES:
            logger.warning("Not enough data for backtest: %d < %d", len(df), settings.WARMUP_CANDLES)
            return []

        # Pre-generate all signals
        signals = self.signal_engine.generate_signals(df, pair)
        signal_map: dict[int, list[Signal]] = {}
        for sig in signals:
            ts = sig.timestamp
            if ts not in signal_map:
                signal_map[ts] = []
            signal_map[ts].append(sig)

        # Process each candle after warmup
        for i in range(settings.WARMUP_CANDLES, len(df)):
            if self.kill_switch.is_killed:
                break

            timestamp = int(df["timestamp"].iloc[i])
            candle_open = df["open"].iloc[i]
            candle_high = df["high"].iloc[i]
            candle_low = df["low"].iloc[i]
            candle_close = df["close"].iloc[i]
            current_atr = df["atr"].iloc[i]

            if pd.isna(current_atr):
                continue

            # Daily reset for kill switch
            day = timestamp // 86_400_000
            if day != self._current_day:
                self._current_day = day
                self.kill_switch.reset_daily(self.capital)

            # --- 1. Process pending entries ---
            self._process_pending_entries(i, candle_high, candle_low, candle_close, current_atr)

            # --- 2. Process open positions (exits) ---
            self._process_exits(candle_high, candle_low, candle_close, current_atr)

            # --- 3. Check kill switch ---
            if self.kill_switch.update(self.capital):
                self._kill_all_positions(candle_close, timestamp)
                break

            # --- 4. Process new signals ---
            bar_signals = signal_map.get(timestamp, [])
            for sig in bar_signals:
                self._process_signal(sig, df, i, candle_close, current_atr)

        return self.trades

    def _process_signal(
        self,
        signal: Signal,
        df: pd.DataFrame,
        bar_index: int,
        current_price: float,
        atr: float,
    ) -> None:
        """Score a signal and potentially create a pending position."""
        scored = calculate_confluence_score(
            signal, df, bar_index,
            threshold=self.confluence_threshold,
        )

        if not scored.should_trade:
            return

        # Check portfolio limits
        allowed, reason = self.portfolio_manager.can_open_position(
            signal.pair, signal.direction, self.positions,
            self.capital, self.capital * settings.RISK_PER_TRADE,
        )
        if not allowed:
            logger.debug("Position blocked: %s", reason)
            return

        # Create pending entry
        pos = self.entry_trailer.create_pending_position(scored, current_price, atr)
        self.positions.append(pos)

    def _process_pending_entries(
        self,
        bar_index: int,
        candle_high: float,
        candle_low: float,
        candle_close: float,
        current_atr: float,
    ) -> None:
        """Update entry trailing and check for fills."""
        for pos in self.positions:
            if pos.state != PositionState.PENDING_ENTRY:
                continue

            self.entry_trailer.update_trailing(pos, candle_close, current_atr)

            if pos.state == PositionState.CANCELLED:
                continue

            if self.entry_trailer.check_entry(pos, candle_low, candle_high):
                # Fill at trigger price + slippage
                if pos.direction == "long":
                    fill_price = pos.entry_trigger * (1 + self.slippage)
                else:
                    fill_price = pos.entry_trigger * (1 - self.slippage)

                # Apply commission
                size = self.position_sizer.calculate_size(
                    self.capital, current_atr, fill_price,
                )
                if size <= 0:
                    pos.state = PositionState.CANCELLED
                    continue

                commission_cost = size * fill_price * self.commission
                self.capital -= commission_cost

                pos.entry_price = fill_price
                pos.entry_time = bar_index
                pos.size = size
                pos.original_size = size
                pos.atr_at_entry = current_atr
                pos.state = PositionState.OPEN

                self.exit_manager.initialize_exit_levels(pos)

                logger.debug(
                    "Entry filled: %s %s @ %.4f, size=%.6f",
                    pos.direction, pos.pair, fill_price, size,
                )

    def _process_exits(
        self,
        candle_high: float,
        candle_low: float,
        candle_close: float,
        current_atr: float,
    ) -> None:
        """Check exit conditions for all open positions."""
        for pos in self.positions:
            if pos.state not in (PositionState.OPEN, PositionState.PARTIAL_TP):
                continue

            prev_state = pos.state
            self.exit_manager.check_exits(pos, candle_high, candle_low, candle_close, current_atr)

            # Handle partial TP PnL
            if prev_state == PositionState.OPEN and pos.state == PositionState.PARTIAL_TP:
                partial_size = pos.original_size * self.exit_manager.partial_tp_ratio
                if pos.direction == "long":
                    partial_price = pos.entry_price + (pos.atr_at_entry * self.exit_manager.partial_tp_atr)
                else:
                    partial_price = pos.entry_price - (pos.atr_at_entry * self.exit_manager.partial_tp_atr)

                partial_pnl = self._calc_pnl(pos.direction, pos.entry_price, partial_price, partial_size)
                commission_cost = partial_size * partial_price * self.commission
                self.capital += partial_pnl - commission_cost

            # Handle full close
            if pos.state in (
                PositionState.CLOSED_SL,
                PositionState.CLOSED_TP,
                PositionState.CLOSED_TRAIL,
                PositionState.KILLED,
            ):
                self._close_position(pos, candle_close)

    def _close_position(self, pos: Position, candle_close: float) -> None:
        """Record a closed position as a TradeRecord."""
        # Determine exit price based on exit type
        if pos.state == PositionState.CLOSED_SL:
            if pos.direction == "long":
                exit_price = pos.stop_loss * (1 - self.slippage)
            else:
                exit_price = pos.stop_loss * (1 + self.slippage)
        elif pos.state == PositionState.CLOSED_TP:
            exit_price = pos.take_profit
        elif pos.state == PositionState.CLOSED_TRAIL:
            if pos.direction == "long":
                exit_price = pos.trailing_stop * (1 - self.slippage)
            else:
                exit_price = pos.trailing_stop * (1 + self.slippage)
        else:
            exit_price = candle_close

        remaining_size = pos.size
        pnl = self._calc_pnl(pos.direction, pos.entry_price, exit_price, remaining_size)
        commission_cost = remaining_size * exit_price * self.commission
        net_pnl = pnl - commission_cost
        self.capital += net_pnl

        pnl_pct = net_pnl / (pos.original_size * pos.entry_price) if pos.entry_price > 0 else 0.0

        exit_reason_map = {
            PositionState.CLOSED_SL: "stop_loss",
            PositionState.CLOSED_TP: "take_profit",
            PositionState.CLOSED_TRAIL: "trailing",
            PositionState.KILLED: "kill_switch",
        }

        trade = TradeRecord(
            trade_id=str(uuid.uuid4())[:8],
            pair=pos.pair,
            direction=pos.direction,
            confluence_score=pos.signal.confluence_score,
            entry_price=pos.entry_price,
            entry_time=pos.entry_time,
            exit_price=exit_price,
            exit_time=0,  # DECISION: use bar_index since we don't track exact timestamp here
            exit_reason=exit_reason_map.get(pos.state, "unknown"),
            position_size=pos.original_size,
            pnl_usd=net_pnl,
            pnl_percent=pnl_pct,
            atr_at_entry=pos.atr_at_entry,
            atr_multiplier=self.exit_manager.atr_multiplier,
            max_favorable_excursion=pos.max_favorable,
            max_adverse_excursion=pos.max_adverse,
            duration_candles=pos.candles_in_trade,
        )

        self.trades.append(trade)
        logger.debug(
            "Trade closed: %s %s, PnL=$%.2f (%.2f%%), reason=%s",
            pos.direction, pos.pair, net_pnl, pnl_pct * 100, trade.exit_reason,
        )

    def _kill_all_positions(self, current_price: float, timestamp: int) -> None:
        """Close all positions due to kill switch activation."""
        for pos in self.positions:
            if pos.state in (PositionState.OPEN, PositionState.PARTIAL_TP):
                pos.state = PositionState.KILLED
                self._close_position(pos, current_price)
            elif pos.state == PositionState.PENDING_ENTRY:
                pos.state = PositionState.CANCELLED

    @staticmethod
    def _calc_pnl(direction: str, entry: float, exit_price: float, size: float) -> float:
        if direction == "long":
            return (exit_price - entry) * size
        else:
            return (entry - exit_price) * size

    def get_metrics(self, trading_days: int = 0) -> BacktestMetrics:
        """Calculate metrics for all completed trades."""
        return calculate_metrics(self.trades, self.initial_capital, trading_days)
