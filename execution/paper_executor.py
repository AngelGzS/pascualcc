"""Paper trading executor — real prices, simulated orders.

Reuses the EXACT same EntryTrailer, ExitManager, PositionSizer, KillSwitch,
and PortfolioManager as BacktestEngine, ensuring paper results are directly
comparable to walk-forward OOS metrics.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from config import settings
from config.types import Position, PositionState, Signal, TradeRecord
from data.fetcher import BinanceFetcher
from data.storage import ParquetStorage
from data.websocket import BinanceKlineWS
from indicators.calculator import calculate_all_indicators
from signals.engine import SignalEngine
from scoring.confluence import calculate_confluence_score
from execution.entry import EntryTrailer
from execution.exit import ExitManager
from risk.position_sizer import PositionSizer
from risk.kill_switch import KillSwitch
from risk.portfolio import PortfolioManager
from paper.state import save_state, load_state
from paper.dashboard import print_dashboard

logger = logging.getLogger(__name__)

# How many candles to keep in the rolling buffer (enough for indicators + warmup)
BUFFER_SIZE = 800


class PaperExecutor:
    """Runs the trading strategy against live Binance prices without placing real orders."""

    def __init__(
        self,
        pair: str = "BTCUSDT",
        timeframe: str = "1h",
        resume: bool = False,
        # WF-optimized params (from last walk-forward window)
        atr_multiplier: float = settings.ATR_MULTIPLIER,
        confluence_threshold: int = settings.CONFLUENCE_THRESHOLD,
        entry_factor: float = settings.ENTRY_FACTOR,
        entry_timeout: int = settings.ENTRY_TIMEOUT,
        tp_multiplier: float = settings.TP_MULTIPLIER,
        pivot_left: int = settings.PIVOT_LEFT,
        pivot_right: int = settings.PIVOT_RIGHT,
    ) -> None:
        self.pair = pair
        self.timeframe = timeframe
        self.resume = resume

        # Capital
        self.initial_capital = settings.INITIAL_CAPITAL
        self.capital = self.initial_capital

        # Commission & slippage (same as backtest)
        self.commission = settings.COMMISSION_RATE
        self.slippage = settings.SLIPPAGE_RATE

        # Strategy components — SAME classes as BacktestEngine
        self.signal_engine = SignalEngine(pivot_left=pivot_left, pivot_right=pivot_right)
        self.entry_trailer = EntryTrailer(entry_factor=entry_factor, entry_timeout=entry_timeout)
        self.exit_manager = ExitManager(atr_multiplier=atr_multiplier, tp_multiplier=tp_multiplier)
        self.position_sizer = PositionSizer(atr_multiplier=atr_multiplier)
        self.kill_switch = KillSwitch()
        self.portfolio_manager = PortfolioManager()
        self.confluence_threshold = confluence_threshold

        # State
        self.positions: list[Position] = []
        self.trades: list[TradeRecord] = []
        self.equity_curve: list[dict[str, Any]] = []
        self.df: pd.DataFrame = pd.DataFrame()
        self.candles_processed = 0
        self.start_time = 0
        self.last_price = 0.0
        self.last_signal_info = "Waiting for first candle..."
        self._current_day = -1
        self._shutdown = False

    async def start(self) -> None:
        """Main entry: load warmup data, optionally resume state, connect WS."""
        logger.info("Starting paper trading: %s %s", self.pair, self.timeframe)

        # Load warmup data
        await self._load_warmup()

        # Resume state if requested
        if self.resume:
            self._restore_state()

        # Initialize kill switch
        self.kill_switch.initialize(self.capital)

        if self.start_time == 0:
            self.start_time = int(datetime.now(timezone.utc).timestamp() * 1000)

        # Show initial dashboard
        self._update_dashboard()

        # Connect to WebSocket
        ws = BinanceKlineWS(
            pair=self.pair,
            timeframe=self.timeframe,
            on_candle=self._on_candle_close,
        )

        # Handle Ctrl+C gracefully
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: self._handle_shutdown(ws))
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                signal.signal(sig, lambda s, f: self._handle_shutdown(ws))

        try:
            await ws.start()
        except asyncio.CancelledError:
            pass
        finally:
            self._shutdown_report()

    async def _load_warmup(self) -> None:
        """Load historical candles for indicator warmup."""
        logger.info("Loading warmup data (%d candles)...", settings.WARMUP_CANDLES)

        storage = ParquetStorage()
        fetcher = BinanceFetcher()

        # Try loading from local storage first
        df = storage.load(self.pair, self.timeframe)

        # Fetch recent candles to ensure data is up-to-date
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        # Need at least BUFFER_SIZE candles for indicators
        timeframe_hours = {"15m": 0.25, "1h": 1, "4h": 4, "1d": 24}.get(self.timeframe, 1)
        start_ms = end_ms - int(BUFFER_SIZE * timeframe_hours * 3600 * 1000)

        if df.empty or len(df) < BUFFER_SIZE:
            logger.info("Fetching %d candles from Binance...", BUFFER_SIZE)
            df = fetcher.fetch_all_klines(
                symbol=self.pair,
                interval=self.timeframe,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        else:
            # Fetch only missing recent candles
            last_ts = int(df["timestamp"].iloc[-1])
            if end_ms - last_ts > timeframe_hours * 3600 * 1000 * 2:
                logger.info("Fetching recent candles to fill gap...")
                recent = fetcher.fetch_all_klines(
                    symbol=self.pair,
                    interval=self.timeframe,
                    start_ms=last_ts + 1,
                    end_ms=end_ms,
                )
                if not recent.empty:
                    df = pd.concat([df, recent], ignore_index=True)
                    df = df.drop_duplicates(subset="timestamp", keep="last").reset_index(drop=True)

        # Keep only last BUFFER_SIZE candles
        if len(df) > BUFFER_SIZE:
            df = df.iloc[-BUFFER_SIZE:].reset_index(drop=True)

        # Calculate indicators
        df = calculate_all_indicators(df)
        self.df = df
        self.last_price = float(df["close"].iloc[-1])

        logger.info(
            "Warmup complete: %d candles loaded, last close=%.2f",
            len(df), self.last_price,
        )

    def _restore_state(self) -> None:
        """Restore state from JSON file."""
        state = load_state(self.pair, self.timeframe)
        if state is None:
            logger.info("No saved state found, starting fresh")
            return

        self.capital = state["capital"]
        self.initial_capital = state["initial_capital"]
        self.trades = state["trades"]
        self.positions = state["positions"]
        self.equity_curve = state["equity_curve"]
        self.start_time = state["start_time"]
        self.candles_processed = state["candles_processed"]

        logger.info(
            "Resumed: capital=$%.2f, %d trades, %d open positions",
            self.capital, len(self.trades),
            sum(1 for p in self.positions if p.state in (PositionState.OPEN, PositionState.PARTIAL_TP)),
        )

    def _on_candle_close(self, candle: dict[str, Any]) -> None:
        """Called by WebSocket when a candle closes. Main processing loop."""
        if self._shutdown or self.kill_switch.is_killed:
            return

        # Append candle to DataFrame
        new_row = pd.DataFrame([candle])
        self.df = pd.concat([self.df, new_row], ignore_index=True)

        # Trim buffer
        if len(self.df) > BUFFER_SIZE:
            self.df = self.df.iloc[-BUFFER_SIZE:].reset_index(drop=True)

        # Recalculate indicators
        self.df = calculate_all_indicators(self.df)

        i = len(self.df) - 1
        timestamp = int(self.df["timestamp"].iloc[i])
        candle_high = float(self.df["high"].iloc[i])
        candle_low = float(self.df["low"].iloc[i])
        candle_close = float(self.df["close"].iloc[i])
        current_atr = float(self.df["atr"].iloc[i])
        self.last_price = candle_close
        self.candles_processed += 1

        if pd.isna(current_atr):
            logger.warning("ATR is NaN at candle %d, skipping", i)
            self._update_dashboard()
            return

        # Daily reset for kill switch
        day = timestamp // 86_400_000
        if day != self._current_day:
            self._current_day = day
            self.kill_switch.reset_daily(self.capital)

        # --- 1. Process pending entries ---
        self._process_pending_entries(candle_high, candle_low, candle_close, current_atr, timestamp)

        # --- 2. Process open positions (exits) ---
        self._process_exits(candle_high, candle_low, candle_close, current_atr, timestamp)

        # --- 3. Check kill switch ---
        if self.kill_switch.update(self.capital):
            self._kill_all_positions(candle_close, timestamp)

        # --- 4. Process new signals ---
        if not self.kill_switch.is_killed:
            self._process_signals(i, candle_close, current_atr)

        # --- 5. Record equity ---
        self.equity_curve.append({
            "timestamp": timestamp,
            "capital": self.capital,
            "price": candle_close,
        })

        # --- 6. Save state periodically ---
        if self.candles_processed % 1 == 0:  # Every candle for paper trading
            self._save()

        # --- 7. Update dashboard ---
        self._update_dashboard()

    def _process_signals(self, bar_index: int, current_price: float, atr: float) -> None:
        """Generate and score signals at current bar."""
        signals = self.signal_engine.generate_signals(self.df, self.pair)

        # Only process signals from the current (last) candle
        current_ts = int(self.df["timestamp"].iloc[bar_index])
        bar_signals = [s for s in signals if s.timestamp == current_ts]

        if not bar_signals:
            self.last_signal_info = (
                f"Last candle: {_format_ts(current_ts)} — No divergence detected"
            )
            return

        for sig in bar_signals:
            scored = calculate_confluence_score(
                sig, self.df, bar_index, threshold=self.confluence_threshold,
            )

            if not scored.should_trade:
                self.last_signal_info = (
                    f"Last signal: {_format_ts(current_ts)} — "
                    f"{sig.direction.upper()} score={scored.confluence_score} (below threshold)"
                )
                continue

            # Check portfolio limits
            allowed, reason = self.portfolio_manager.can_open_position(
                sig.pair, sig.direction, self.positions,
                self.capital, self.capital * settings.RISK_PER_TRADE,
            )
            if not allowed:
                self.last_signal_info = (
                    f"Last signal: {_format_ts(current_ts)} — "
                    f"{sig.direction.upper()} score={scored.confluence_score} BLOCKED: {reason}"
                )
                continue

            # Create pending entry
            pos = self.entry_trailer.create_pending_position(scored, current_price, atr)
            self.positions.append(pos)

            self.last_signal_info = (
                f"NEW SIGNAL: {_format_ts(current_ts)} — "
                f"{sig.direction.upper()} score={scored.confluence_score} "
                f"[{scored.confidence}] trigger=${pos.entry_trigger:,.2f}"
            )
            logger.info(
                "Paper signal: %s %s score=%d trigger=%.2f",
                sig.direction, sig.pair, scored.confluence_score, pos.entry_trigger,
            )

    def _process_pending_entries(
        self, candle_high: float, candle_low: float, candle_close: float,
        current_atr: float, timestamp: int,
    ) -> None:
        """Update entry trailing and check for fills — same logic as BacktestEngine."""
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

                size = self.position_sizer.calculate_size(self.capital, current_atr, fill_price)
                if size <= 0:
                    pos.state = PositionState.CANCELLED
                    continue

                commission_cost = size * fill_price * self.commission
                self.capital -= commission_cost

                pos.entry_price = fill_price
                pos.entry_time = timestamp
                pos.size = size
                pos.original_size = size
                pos.atr_at_entry = current_atr
                pos.state = PositionState.OPEN

                self.exit_manager.initialize_exit_levels(pos)

                logger.info(
                    "PAPER FILL: %s %s @ $%.2f, size=%.6f, SL=$%.2f, TP=$%.2f",
                    pos.direction.upper(), pos.pair, fill_price, size,
                    pos.stop_loss, pos.take_profit,
                )

    def _process_exits(
        self, candle_high: float, candle_low: float, candle_close: float,
        current_atr: float, timestamp: int,
    ) -> None:
        """Check exit conditions — same logic as BacktestEngine."""
        for pos in self.positions:
            if pos.state not in (PositionState.OPEN, PositionState.PARTIAL_TP):
                continue

            prev_state = pos.state
            self.exit_manager.check_exits(pos, candle_high, candle_low, candle_close, current_atr)

            # Handle partial TP
            if prev_state == PositionState.OPEN and pos.state == PositionState.PARTIAL_TP:
                partial_size = pos.original_size * self.exit_manager.partial_tp_ratio
                if pos.direction == "long":
                    partial_price = pos.entry_price + (pos.atr_at_entry * self.exit_manager.partial_tp_atr)
                else:
                    partial_price = pos.entry_price - (pos.atr_at_entry * self.exit_manager.partial_tp_atr)

                partial_pnl = self._calc_pnl(pos.direction, pos.entry_price, partial_price, partial_size)
                commission_cost = partial_size * partial_price * self.commission
                self.capital += partial_pnl - commission_cost
                logger.info("PAPER PARTIAL TP: %s $%.2f", pos.pair, partial_pnl - commission_cost)

            # Handle full close
            if pos.state in (
                PositionState.CLOSED_SL, PositionState.CLOSED_TP,
                PositionState.CLOSED_TRAIL, PositionState.KILLED,
            ):
                self._close_position(pos, candle_close, timestamp)

    def _close_position(self, pos: Position, candle_close: float, timestamp: int) -> None:
        """Record a closed position — same PnL logic as BacktestEngine."""
        if pos.state == PositionState.CLOSED_SL:
            exit_price = pos.stop_loss * (1 - self.slippage if pos.direction == "long" else 1 + self.slippage)
        elif pos.state == PositionState.CLOSED_TP:
            exit_price = pos.take_profit
        elif pos.state == PositionState.CLOSED_TRAIL:
            exit_price = pos.trailing_stop * (1 - self.slippage if pos.direction == "long" else 1 + self.slippage)
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
            exit_time=timestamp,
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

        logger.info(
            "PAPER CLOSE: %s %s, entry=$%.2f exit=$%.2f, PnL=$%.2f (%.2f%%), reason=%s",
            pos.direction.upper(), pos.pair,
            pos.entry_price, exit_price,
            net_pnl, pnl_pct * 100,
            trade.exit_reason,
        )

    def _kill_all_positions(self, current_price: float, timestamp: int) -> None:
        """Close all positions due to kill switch."""
        for pos in self.positions:
            if pos.state in (PositionState.OPEN, PositionState.PARTIAL_TP):
                pos.state = PositionState.KILLED
                self._close_position(pos, current_price, timestamp)
            elif pos.state == PositionState.PENDING_ENTRY:
                pos.state = PositionState.CANCELLED
        logger.warning("KILL SWITCH activated — all positions closed")

    @staticmethod
    def _calc_pnl(direction: str, entry: float, exit_price: float, size: float) -> float:
        if direction == "long":
            return (exit_price - entry) * size
        return (entry - exit_price) * size

    def _save(self) -> None:
        """Persist state to disk."""
        save_state(
            pair=self.pair,
            timeframe=self.timeframe,
            capital=self.capital,
            initial_capital=self.initial_capital,
            positions=self.positions,
            trades=self.trades,
            equity_curve=self.equity_curve,
            start_time=self.start_time,
            candles_processed=self.candles_processed,
        )

    def _update_dashboard(self) -> None:
        """Refresh console output."""
        print_dashboard(
            pair=self.pair,
            timeframe=self.timeframe,
            capital=self.capital,
            initial_capital=self.initial_capital,
            positions=self.positions,
            trades=self.trades,
            start_time=self.start_time,
            candles_processed=self.candles_processed,
            last_price=self.last_price,
            last_signal_info=self.last_signal_info,
        )

    def _handle_shutdown(self, ws: BinanceKlineWS) -> None:
        """Graceful shutdown on Ctrl+C."""
        logger.info("Shutdown signal received...")
        self._shutdown = True
        ws.stop()

    def _shutdown_report(self) -> None:
        """Print final report and save state."""
        self._save()
        print("\n" + "=" * 60)
        print("  PAPER TRADING STOPPED")
        print("=" * 60)
        print(f"  Capital: ${self.capital:,.2f}")
        pnl = self.capital - self.initial_capital
        pnl_pct = pnl / self.initial_capital * 100
        print(f"  PnL: ${pnl:+,.2f} ({pnl_pct:+.2f}%)")
        print(f"  Total trades: {len(self.trades)}")
        if self.trades:
            wins = sum(1 for t in self.trades if t.pnl_usd > 0)
            print(f"  Win rate: {wins / len(self.trades) * 100:.0f}%")
        print(f"  State saved. Resume with: python main.py paper --resume")
        print("=" * 60)


def _format_ts(ts_ms: int) -> str:
    """Format a timestamp for display."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
