"""ORB (Opening Range Breakout) live paper trading executor.

Connects to BingX or Binance WebSocket for live 15m candles (controlled by PRICE_SOURCE env var).
Generates ORB signals at NY session open window.
Manages trades with fixed risk sizing.
Persists state to JSON for crash recovery.
Exposes /api/status for web dashboard.
"""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from config import settings
from data.fetcher import BinanceFetcher, BingXFetcher
from data.websocket import BinanceKlineWS, BingXKlineWS
from data.kline_poller import BingXKlinePoller
from paper.state import save_state, load_state
from strategies.intraday.session_utils import to_est

logger = logging.getLogger(__name__)

BUFFER_SIZE = 200  # 15m candles (~2 days, enough for ORB)
STATE_DIR = Path("data/paper")


class ORBPaperExecutor:
    """Live paper trading for the ORB strategy."""

    def __init__(
        self,
        pair: str = "BTCUSDT",
        initial_capital: float = 10_000.0,
        risk_per_trade: float = 0.02,
        rr_target: float = 2.5,
        fixed_risk: bool = True,
        commission: float = 0.001,
        slippage: float = 0.0005,
        resume: bool = False,
        strategy: str = "orb",
    ) -> None:
        self.pair = pair
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.rr_target = rr_target
        self.fixed_risk = fixed_risk
        self.commission = commission
        self.slippage = slippage
        self.resume = resume
        self.strategy = strategy

        # ORB state
        self.or_high: float | None = None
        self.or_low: float | None = None
        self.or_formed = False
        self.or_day: str = ""
        self.today_traded = False

        # Position tracking
        self.open_position: dict | None = None  # current open trade
        self.trades: list[dict] = []
        self.candles_processed = 0
        self.start_time = int(datetime.now(timezone.utc).timestamp() * 1000)

        # Data
        self.df = pd.DataFrame()
        self._shutdown = False

    @property
    def state_file(self) -> Path:
        return STATE_DIR / f"orb_{self.pair}_state.json"

    # ─── Main loop ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        source = settings.PRICE_SOURCE
        logger.info("Starting ORB paper trading: %s (capital=$%.0f, risk=%.0f%%, RR=%.1f, strategy=%s, source=%s)",
                     self.pair, self.initial_capital, self.risk_per_trade * 100, self.rr_target, self.strategy, source)

        await self._load_warmup()

        if self.resume:
            self._restore_state()

        self._print_status()

        if source == "binance":
            ws = BinanceKlineWS(
                pair=self.pair,
                timeframe="15m",
                on_candle=self._on_candle_close,
            )
        else:
            # Try WebSocket first, fall back to REST poller
            use_poller = os.environ.get("BINGX_USE_POLLER", "false").lower() == "true"
            if use_poller:
                logger.info("Using REST API poller (WS disabled via BINGX_USE_POLLER)")
                ws = BingXKlinePoller(
                    pair=self.pair,
                    timeframe="15m",
                    on_candle=self._on_candle_close,
                )
            else:
                ws = BingXKlineWS(
                    pair=self.pair,
                    timeframe="15m",
                    on_candle=self._on_candle_close,
                )

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: self._handle_shutdown(ws))
            except NotImplementedError:
                signal.signal(sig, lambda s, f: self._handle_shutdown(ws))

        try:
            await ws.start()
        except asyncio.CancelledError:
            pass
        finally:
            self._save_state()
            self._print_final_report()

    async def _on_candle_close(self, candle: dict) -> None:
        """Called on every 15m candle close."""
        ts = int(candle["t"])
        o = float(candle["o"])
        h = float(candle["h"])
        l = float(candle["l"])
        c = float(candle["c"])
        v = float(candle["v"])

        # Append to buffer
        new_row = {"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}
        self.df = pd.concat([self.df, pd.DataFrame([new_row])], ignore_index=True)
        if len(self.df) > BUFFER_SIZE:
            self.df = self.df.iloc[-BUFFER_SIZE:].reset_index(drop=True)

        self.candles_processed += 1
        est = to_est(ts)
        est_h = est.hour
        est_m = est.minute
        day = est.strftime("%Y-%m-%d")
        weekday = est.weekday()

        # Skip weekends
        if weekday >= 5:
            return

        # New day reset
        if day != self.or_day:
            self.or_day = day
            self.or_high = None
            self.or_low = None
            self.or_formed = False
            self.today_traded = False

        # ─── Check open position ────────────────────────────────────────
        if self.open_position:
            self._check_exit(h, l, c, ts)
            self._save_state()
            self._print_status()
            return

        # ─── Build Opening Range (9:30-10:00 EST = 2 candles of 15m) ────
        if est_h == 9 and est_m >= 30 and not self.or_formed:
            if self.or_high is None:
                self.or_high = h
                self.or_low = l
                logger.info("[%s] OR building: H=%.2f L=%.2f", est.strftime("%H:%M"), h, l)
            else:
                self.or_high = max(self.or_high, h)
                self.or_low = min(self.or_low, l)
                logger.info("[%s] OR building: H=%.2f L=%.2f", est.strftime("%H:%M"), self.or_high, self.or_low)

        # OR forms at 10:00 EST (after 9:45 candle closes)
        if est_h == 10 and est_m == 0 and self.or_high and not self.or_formed:
            self.or_formed = True
            or_range = self.or_high - self.or_low
            or_pct = or_range / self.or_low * 100
            logger.info("[%s] OR FORMED: H=%.2f L=%.2f Range=%.2f (%.3f%%)",
                        day, self.or_high, self.or_low, or_range, or_pct)

            # Min range filter
            if or_pct < 0.05:
                logger.info("[%s] OR too small (%.4f%%), skipping", day, or_pct)
                self.or_formed = False

        # ─── Entry window: 10:00-13:00 EST ──────────────────────────────
        if not self.or_formed or self.today_traded:
            return
        if est_h < 10 or est_h >= 13:
            return

        # LONG: close above OR high
        if c > self.or_high:
            self._open_trade("long", c, ts, est)
        # SHORT: close below OR low
        elif c < self.or_low:
            self._open_trade("short", c, ts, est)

        self._save_state()
        self._print_status()

    # ─── Trade management ───────────────────────────────────────────────

    def _open_trade(self, direction: str, price: float, ts: int, est: datetime) -> None:
        if self.today_traded or self.open_position:
            return

        # Apply slippage
        if direction == "long":
            fill = price * (1 + self.slippage)
            sl = self.or_low * (1 - self.slippage)
            risk_dist = fill - sl
            tp = fill + risk_dist * self.rr_target
        else:
            fill = price * (1 - self.slippage)
            sl = self.or_high * (1 + self.slippage)
            risk_dist = sl - fill
            tp = fill - risk_dist * self.rr_target

        if risk_dist <= 0:
            return

        # Position sizing (fixed risk)
        base_capital = self.initial_capital if self.fixed_risk else self.capital
        risk_amount = base_capital * self.risk_per_trade
        risk_fraction = risk_dist / fill
        position_size = risk_amount / risk_fraction

        self.open_position = {
            "direction": direction,
            "entry_price": fill,
            "stop_loss": sl,
            "take_profit": tp,
            "position_size": position_size,
            "risk_amount": risk_amount,
            "entry_time": ts,
            "or_high": self.or_high,
            "or_low": self.or_low,
        }
        self.today_traded = True

        logger.info(">>> ENTRY %s %.2f | SL=%.2f TP=%.2f | Size=$%.0f Risk=$%.0f",
                     direction.upper(), fill, sl, tp, position_size, risk_amount)

    def _check_exit(self, high: float, low: float, close: float, ts: int) -> None:
        pos = self.open_position
        if not pos:
            return

        d = pos["direction"]
        exit_price = 0.0
        exit_reason = ""

        # Check SL first
        if d == "long" and low <= pos["stop_loss"]:
            exit_price = pos["stop_loss"]
            exit_reason = "sl"
        elif d == "short" and high >= pos["stop_loss"]:
            exit_price = pos["stop_loss"]
            exit_reason = "sl"
        # Check TP
        elif d == "long" and high >= pos["take_profit"]:
            exit_price = pos["take_profit"]
            exit_reason = "tp"
        elif d == "short" and low <= pos["take_profit"]:
            exit_price = pos["take_profit"]
            exit_reason = "tp"

        if not exit_reason:
            return

        # Calculate PnL
        if d == "long":
            pct = (exit_price - pos["entry_price"]) / pos["entry_price"]
        else:
            pct = (pos["entry_price"] - exit_price) / pos["entry_price"]

        gross = pos["position_size"] * pct
        comm = pos["position_size"] * self.commission * 2
        net = gross - comm
        r_mult = net / pos["risk_amount"] if pos["risk_amount"] > 0 else 0

        # Cap at -1R
        if net < -pos["risk_amount"]:
            net = -pos["risk_amount"]
            r_mult = -1.0

        self.capital += net

        trade = {
            "direction": d,
            "entry_price": pos["entry_price"],
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "pnl_usd": net,
            "r_multiple": r_mult,
            "entry_time": pos["entry_time"],
            "exit_time": ts,
            "or_high": pos["or_high"],
            "or_low": pos["or_low"],
        }
        self.trades.append(trade)
        self.open_position = None

        tag = "WIN" if net > 0 else "LOSS"
        logger.info("<<< EXIT %s %s %.2f | PnL=$%.2f (%.1fR) | Capital=$%.2f",
                     tag, d.upper(), exit_price, net, r_mult, self.capital)

    # ─── State persistence ──────────────────────────────────────────────

    def _save_state(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "pair": self.pair,
            "strategy": self.strategy,
            "initial_capital": self.initial_capital,
            "capital": self.capital,
            "risk_per_trade": self.risk_per_trade,
            "rr_target": self.rr_target,
            "fixed_risk": self.fixed_risk,
            "candles_processed": self.candles_processed,
            "start_time": self.start_time,
            "or_high": self.or_high,
            "or_low": self.or_low,
            "or_formed": self.or_formed,
            "or_day": self.or_day,
            "today_traded": self.today_traded,
            "open_position": self.open_position,
            "trades": self.trades,
        }
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(self.state_file)

    def _restore_state(self) -> None:
        if not self.state_file.exists():
            logger.info("No state file found, starting fresh")
            return
        state = json.loads(self.state_file.read_text())
        self.capital = state.get("capital", self.initial_capital)
        self.candles_processed = state.get("candles_processed", 0)
        self.start_time = state.get("start_time", self.start_time)
        self.or_high = state.get("or_high")
        self.or_low = state.get("or_low")
        self.or_formed = state.get("or_formed", False)
        self.or_day = state.get("or_day", "")
        self.today_traded = state.get("today_traded", False)
        self.open_position = state.get("open_position")
        self.trades = state.get("trades", [])
        logger.info("Restored state: capital=$%.2f, %d trades, %d candles",
                     self.capital, len(self.trades), self.candles_processed)

    # ─── Data loading ───────────────────────────────────────────────────

    async def _load_warmup(self) -> None:
        source = settings.PRICE_SOURCE
        logger.info("Loading warmup data from %s...", source)
        if source == "binance":
            fetcher = BinanceFetcher()
        else:
            fetcher = BingXFetcher()
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = end_ms - int(BUFFER_SIZE * 15 * 60 * 1000)

        df = fetcher.fetch_all_klines(
            symbol=self.pair, interval="15m",
            start_ms=start_ms, end_ms=end_ms,
        )
        if df.empty:
            logger.error("Failed to load warmup data from %s!", source)
            return
        self.df = df.tail(BUFFER_SIZE).reset_index(drop=True)
        logger.info("Loaded %d warmup candles from %s", len(self.df), source)

    # ─── Display ────────────────────────────────────────────────────────

    def _print_status(self) -> None:
        pnl = self.capital - self.initial_capital
        pnl_pct = pnl / self.initial_capital * 100
        n = len(self.trades)
        wins = sum(1 for t in self.trades if t["pnl_usd"] > 0)
        wr = wins / n * 100 if n > 0 else 0

        status = (
            f"\n{'=' * 60}\n"
            f"  ORB Paper Trading | {self.pair} | {self.strategy}\n"
            f"  Capital: ${self.capital:,.2f} (PnL: ${pnl:+,.2f} / {pnl_pct:+.1f}%)\n"
            f"  Trades: {n} | WR: {wr:.0f}% | Candles: {self.candles_processed}\n"
        )

        if self.or_formed:
            status += f"  OR: H={self.or_high:.2f} L={self.or_low:.2f} (traded={self.today_traded})\n"

        if self.open_position:
            pos = self.open_position
            status += (
                f"  OPEN: {pos['direction'].upper()} @ {pos['entry_price']:.2f}"
                f" | SL={pos['stop_loss']:.2f} TP={pos['take_profit']:.2f}\n"
            )

        if self.trades:
            last = self.trades[-1]
            tag = "W" if last["pnl_usd"] > 0 else "L"
            dt = datetime.fromtimestamp(last["exit_time"] / 1000, tz=timezone.utc)
            status += f"  Last: [{tag}] {last['direction'].upper()} ${last['pnl_usd']:+.2f} ({last['r_multiple']:+.1f}R) {dt.strftime('%m-%d %H:%M')}\n"

        status += f"{'=' * 60}"
        print(status)

    def _print_final_report(self) -> None:
        n = len(self.trades)
        if n == 0:
            print("\nNo trades executed.")
            return

        wins = [t for t in self.trades if t["pnl_usd"] > 0]
        losses = [t for t in self.trades if t["pnl_usd"] <= 0]
        total_pnl = sum(t["pnl_usd"] for t in self.trades)
        gross_p = sum(t["pnl_usd"] for t in wins)
        gross_l = abs(sum(t["pnl_usd"] for t in losses))
        pf = gross_p / gross_l if gross_l > 0 else float("inf")

        print(f"\n{'=' * 60}")
        print(f"  FINAL REPORT | {self.pair}")
        print(f"{'=' * 60}")
        print(f"  Trades: {n} | Wins: {len(wins)} | Losses: {len(losses)}")
        print(f"  Win rate: {len(wins) / n * 100:.0f}%")
        print(f"  Profit factor: {pf:.2f}")
        print(f"  Total PnL: ${total_pnl:+,.2f} ({total_pnl / self.initial_capital * 100:+.1f}%)")
        print(f"  Capital: ${self.capital:,.2f}")
        for t in self.trades:
            dt = datetime.fromtimestamp(t["entry_time"] / 1000, tz=timezone.utc)
            tag = "[W]" if t["pnl_usd"] > 0 else "[L]"
            print(f"    {tag} {dt.strftime('%m-%d %H:%M')} {t['direction'].upper():5} ${t['pnl_usd']:+9.2f} ({t['r_multiple']:+.1f}R) {t['exit_reason']}")
        print(f"{'=' * 60}\n")

    def get_status_dict(self) -> dict:
        """Return status as dict for web API."""
        n = len(self.trades)
        wins = sum(1 for t in self.trades if t["pnl_usd"] > 0)
        pnl = self.capital - self.initial_capital

        return {
            "pair": self.pair,
            "strategy": self.strategy,
            "capital": self.capital,
            "initial_capital": self.initial_capital,
            "pnl_usd": pnl,
            "pnl_pct": pnl / self.initial_capital * 100,
            "trades_count": n,
            "win_rate": wins / n * 100 if n > 0 else 0,
            "candles_processed": self.candles_processed,
            "or_high": self.or_high,
            "or_low": self.or_low,
            "or_formed": self.or_formed,
            "today_traded": self.today_traded,
            "open_position": self.open_position,
            "recent_trades": self.trades[-10:],
            "uptime_hours": (datetime.now(timezone.utc).timestamp() * 1000 - self.start_time) / 3600000,
        }

    # ─── Shutdown ───────────────────────────────────────────────────────

    def _handle_shutdown(self, ws: BinanceKlineWS | BingXKlineWS) -> None:
        logger.info("Shutdown requested, saving state...")
        self._shutdown = True
        self._save_state()
        asyncio.ensure_future(ws.stop())
