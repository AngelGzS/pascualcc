"""Live paper copy-trading from Telegram signals."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from config import settings
from data.fetcher import BinanceFetcher
from telegram.backtester import CopyTradeRecord
from telegram.client import TelegramListener
from telegram.parser import TelegramSignal, ManagementUpdate, parse_signal, parse_management

logger = logging.getLogger(__name__)


class CopyPosition:
    """Tracks an open copy-trade position with multiple targets."""

    def __init__(self, signal: TelegramSignal, entry_price: float, margin: float) -> None:
        self.id = str(uuid.uuid4())[:8]
        self.signal = signal
        self.pair = signal.pair
        self.direction = signal.direction
        self.leverage = signal.leverage
        self.entry_price = entry_price
        self.entry_time = int(datetime.now(timezone.utc).timestamp() * 1000)
        self.margin = margin
        self.notional = margin * signal.leverage
        self.stop_loss = signal.stop_loss
        self.targets = list(signal.targets)
        self.targets_hit = 0
        self.remaining_pct = 1.0
        self.realized_pnl = 0.0
        self.closed = False
        self.exit_reason = ""
        self.exit_time = 0
        self.msg_id: int | None = None  # Telegram message ID for reply matching


class CopyTradeExecutor:
    """Paper copy-trader: reads Telegram signals, simulates positions with live prices."""

    def __init__(
        self,
        channel_name: str,
        initial_capital: float = settings.INITIAL_CAPITAL,
        risk_per_trade: float = settings.RISK_PER_TRADE,
        resume: bool = False,
    ) -> None:
        self.channel_name = channel_name
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.resume = resume

        self.positions: list[CopyPosition] = []
        self.trades: list[CopyTradeRecord] = []
        self.fetcher = BinanceFetcher()
        self._shutdown = False
        self._state_path = os.path.join(settings.COPY_STATE_DIR, "copy_state.json")

        # Map signal msg_id → position for reply matching
        self._signal_msg_map: dict[int, CopyPosition] = {}

    async def start(self) -> None:
        """Start listening to Telegram and price updates."""
        os.makedirs(settings.COPY_STATE_DIR, exist_ok=True)

        if self.resume:
            self._load_state()

        logger.info(
            "Copy trader starting: channel=%s, capital=$%.2f",
            self.channel_name, self.capital,
        )

        # Start price monitoring in background
        price_task = asyncio.create_task(self._price_loop())

        # Start Telegram listener (blocks until disconnect)
        listener = TelegramListener(
            channel_name=self.channel_name,
            on_message=self._on_message,
        )

        try:
            await listener.start()
        except asyncio.CancelledError:
            pass
        finally:
            price_task.cancel()
            self._save_state()
            self._print_summary()

    def _on_message(self, text: str, timestamp_ms: int, reply_to_msg_id: int | None) -> None:
        """Handle a new message from Telegram."""
        # Try parsing as a signal first
        signal = parse_signal(text, timestamp=timestamp_ms)
        if signal is not None:
            self._handle_signal(signal, timestamp_ms)
            return

        # Try parsing as management update (must be a reply)
        if reply_to_msg_id is not None:
            update = parse_management(text)
            if update is not None:
                self._handle_management(update, reply_to_msg_id)

    def _handle_signal(self, signal: TelegramSignal, msg_timestamp: int) -> None:
        """Open a new paper position from a signal."""
        # Get current price
        try:
            current_price = self.fetcher.get_ticker_price(signal.pair)
        except Exception as e:
            logger.error("Could not get price for %s: %s", signal.pair, e)
            return

        margin = self.capital * self.risk_per_trade
        pos = CopyPosition(signal, entry_price=current_price, margin=margin)

        self.positions.append(pos)
        self.capital -= 0  # Paper: no actual deduction, PnL tracked separately

        logger.info(
            "NEW POSITION: %s %s %dx @ $%.4f, SL=$%.4f, TPs=%s, margin=$%.2f",
            pos.direction.upper(), pos.pair, pos.leverage,
            pos.entry_price, pos.stop_loss, pos.targets, pos.margin,
        )

        self._save_state()
        self._print_dashboard()

    def _handle_management(self, update: ManagementUpdate, reply_to_msg_id: int) -> None:
        """Handle a management update for an existing position."""
        pos = self._signal_msg_map.get(reply_to_msg_id)
        if pos is None or pos.closed:
            logger.debug("Management update for unknown/closed signal (msg_id=%d)", reply_to_msg_id)
            return

        if update.action == "close_all":
            try:
                price = self.fetcher.get_ticker_price(pos.pair)
            except Exception:
                price = pos.entry_price
            self._close_position(pos, price, "manual_close")
        elif update.action == "partial_close" and update.percentage > 0:
            logger.info("Management: close %.0f%% of %s", update.percentage * 100, pos.pair)
        if update.sl_to_breakeven:
            pos.stop_loss = pos.entry_price
            logger.info("Management: SL moved to breakeven for %s", pos.pair)
        elif update.new_sl is not None:
            pos.stop_loss = update.new_sl
            logger.info("Management: SL moved to %.4f for %s", update.new_sl, pos.pair)

        self._save_state()

    async def _price_loop(self) -> None:
        """Periodically check prices for all open positions."""
        while not self._shutdown:
            open_positions = [p for p in self.positions if not p.closed]
            for pos in open_positions:
                try:
                    price = self.fetcher.get_ticker_price(pos.pair)
                    self._check_position(pos, price)
                except Exception as e:
                    logger.warning("Price check failed for %s: %s", pos.pair, e)

            if open_positions:
                self._print_dashboard()

            await asyncio.sleep(30)  # Check every 30 seconds

    def _check_position(self, pos: CopyPosition, current_price: float) -> None:
        """Check TP and SL for a position."""
        if pos.closed:
            return

        # --- Stop loss ---
        sl_hit = False
        if pos.direction == "long" and current_price <= pos.stop_loss:
            sl_hit = True
        elif pos.direction == "short" and current_price >= pos.stop_loss:
            sl_hit = True

        if sl_hit:
            pnl = self._calc_chunk_pnl(pos, pos.stop_loss, pos.remaining_pct)
            pos.realized_pnl += pnl
            self._close_position(pos, pos.stop_loss, "stop_loss")
            return

        # --- Check targets ---
        # Sort targets for direction
        targets = sorted(pos.targets)
        if pos.direction == "short":
            targets = sorted(pos.targets, reverse=True)

        while pos.targets_hit < len(targets):
            tp = targets[pos.targets_hit]
            tp_hit = False

            if pos.direction == "long" and current_price >= tp:
                tp_hit = True
            elif pos.direction == "short" and current_price <= tp:
                tp_hit = True

            if not tp_hit:
                break

            # Close 25%
            close_pct = min(0.25, pos.remaining_pct)
            pnl = self._calc_chunk_pnl(pos, tp, close_pct)
            pos.realized_pnl += pnl
            pos.remaining_pct -= close_pct
            pos.targets_hit += 1

            logger.info(
                "TP%d HIT for %s %s @ $%.4f, PnL=$%.2f, remaining=%.0f%%",
                pos.targets_hit, pos.direction.upper(), pos.pair,
                tp, pnl, pos.remaining_pct * 100,
            )

            # After TP1: SL → breakeven
            if pos.targets_hit == 1:
                pos.stop_loss = pos.entry_price
                logger.info("SL moved to breakeven for %s", pos.pair)

            # All targets hit
            if pos.remaining_pct <= 0.001:
                self._close_position(pos, tp, f"tp{pos.targets_hit}")
                return

        self._save_state()

    def _calc_chunk_pnl(self, pos: CopyPosition, exit_price: float, pct: float) -> float:
        """Calculate PnL for a chunk of the position."""
        chunk_notional = pos.notional * pct
        if pos.direction == "long":
            return chunk_notional * (exit_price - pos.entry_price) / pos.entry_price
        else:
            return chunk_notional * (pos.entry_price - exit_price) / pos.entry_price

    def _close_position(self, pos: CopyPosition, exit_price: float, reason: str) -> None:
        """Mark position as closed and record trade."""
        pos.closed = True
        pos.exit_reason = reason
        pos.exit_time = int(datetime.now(timezone.utc).timestamp() * 1000)

        self.capital += pos.realized_pnl
        pnl_pct = pos.realized_pnl / pos.margin if pos.margin > 0 else 0

        trade = CopyTradeRecord(
            pair=pos.pair,
            direction=pos.direction,
            leverage=pos.leverage,
            entry_price=pos.entry_price,
            entry_time=pos.entry_time,
            exit_price=exit_price,
            exit_time=pos.exit_time,
            exit_reason=reason,
            targets=pos.targets,
            targets_hit=pos.targets_hit,
            stop_loss=pos.signal.stop_loss,
            margin_used=pos.margin,
            pnl_usd=pos.realized_pnl,
            pnl_percent=pnl_pct,
            duration_hours=(pos.exit_time - pos.entry_time) / 3_600_000,
        )
        self.trades.append(trade)

        logger.info(
            "CLOSED: %s %s, PnL=$%.2f (%.1f%%), reason=%s, capital=$%.2f",
            pos.direction.upper(), pos.pair,
            pos.realized_pnl, pnl_pct * 100,
            reason, self.capital,
        )

        self._save_state()
        self._print_dashboard()

    def _print_dashboard(self) -> None:
        """Print current status to console."""
        open_pos = [p for p in self.positions if not p.closed]
        pnl = self.capital - self.initial_capital
        pnl_pct = pnl / self.initial_capital * 100

        print(f"\n{'=' * 55}")
        print(f"  COPY TRADER  |  {self.channel_name}")
        print(f"{'=' * 55}")
        print(f"  Capital: ${self.capital:,.2f} ({pnl_pct:+.2f}%)")
        print(f"  Trades: {len(self.trades)} | Open: {len(open_pos)}")

        if self.trades:
            wins = sum(1 for t in self.trades if t.pnl_usd > 0)
            print(f"  WR: {wins / len(self.trades) * 100:.0f}%")

        for p in open_pos:
            try:
                price = self.fetcher.get_ticker_price(p.pair)
                unrealized = self._calc_chunk_pnl(p, price, p.remaining_pct)
            except Exception:
                price = 0
                unrealized = 0
            print(
                f"  [{p.direction.upper()}] {p.pair} {p.leverage}x "
                f"@ ${p.entry_price:.4f} "
                f"TP{p.targets_hit}/{len(p.targets)} "
                f"PnL=${unrealized:+.2f}"
            )

        print(f"{'=' * 55}")

    def _save_state(self) -> None:
        """Save state to JSON."""
        state = {
            "capital": self.capital,
            "initial_capital": self.initial_capital,
            "trades": [asdict(t) for t in self.trades],
            "positions": [self._pos_to_dict(p) for p in self.positions if not p.closed],
        }
        try:
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error("Failed to save state: %s", e)

    def _load_state(self) -> None:
        """Load state from JSON."""
        if not os.path.exists(self._state_path):
            return
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.capital = data.get("capital", self.initial_capital)
            self.trades = [CopyTradeRecord(**t) for t in data.get("trades", [])]
            logger.info("Resumed: capital=$%.2f, %d trades", self.capital, len(self.trades))
        except Exception as e:
            logger.error("Failed to load state: %s", e)

    @staticmethod
    def _pos_to_dict(pos: CopyPosition) -> dict:
        return {
            "pair": pos.pair, "direction": pos.direction,
            "leverage": pos.leverage, "entry_price": pos.entry_price,
            "entry_time": pos.entry_time, "margin": pos.margin,
            "stop_loss": pos.stop_loss, "targets": pos.targets,
            "targets_hit": pos.targets_hit, "remaining_pct": pos.remaining_pct,
            "realized_pnl": pos.realized_pnl,
        }

    def _print_summary(self) -> None:
        """Print final summary on shutdown."""
        pnl = self.capital - self.initial_capital
        print(f"\n{'=' * 55}")
        print("  COPY TRADER STOPPED")
        print(f"  Capital: ${self.capital:,.2f} (${pnl:+,.2f})")
        print(f"  Trades: {len(self.trades)}")
        print(f"  State saved. Resume with --resume")
        print(f"{'=' * 55}")
