"""PO3 strategy configuration."""
from __future__ import annotations

# ─── Session times (EST / America/New_York) ─────────────────────────────────
BIAS_CANDLE_HOUR: int = 6          # 6:00 AM EST — bias candle
ENTRY_WINDOW_START: int = 10       # 10:00 AM EST — start of entry window
ENTRY_WINDOW_END: int = 14         # 2:00 PM EST — end of entry window
EST_TIMEZONE: str = "America/New_York"

# Only trade Mon-Fri (real market hours)
TRADE_WEEKDAYS_ONLY: bool = True

# ─── FVG (Fair Value Gap) ────────────────────────────────────────────────────
FVG_MIN_SIZE_PERCENT: float = 0.0005   # Min FVG size as fraction of price
FVG_MAX_AGE_CANDLES: int = 50          # Ignore FVGs older than this

# ─── Sweep detection ────────────────────────────────────────────────────────
SWEEP_LOOKBACK: int = 20               # Candles to look back for swing levels
PIVOT_LEFT: int = 3                    # Left bars for pivot detection
PIVOT_RIGHT: int = 3                   # Right bars for pivot detection

# ─── CISD (Change in State of Delivery) ─────────────────────────────────────
DISPLACEMENT_BODY_MULT: float = 1.5    # Body > 1.5x avg body = displacement
DISPLACEMENT_LOOKBACK: int = 20        # Candles to average body size over

# ─── Entry / Exit ────────────────────────────────────────────────────────────
DEFAULT_RR: float = 2.0                # Default R:R target (1:2)
MAX_RR: float = 3.0                    # Stretch R:R target (1:3)
ENTRY_TIMEOUT_CANDLES: int = 8         # Max 15m candles to wait for fill

# ─── Risk ────────────────────────────────────────────────────────────────────
RISK_PER_TRADE: float = 0.02           # 2% of capital per trade
MAX_DAILY_TRADES: int = 2              # Max trades per day

# ─── Symbol mapping (friendly name → BingX API symbol) ──────────────────────
INDEX_SYMBOLS: dict[str, str] = {
    # Crypto (use Binance spot as proxy — same price action)
    "BTCUSDT": "BTCUSDT",
    "ETHUSDT": "ETHUSDT",
    "SPX": "SPX-USDT",
    # BingX indices
    "SPX500": "NCSISP5002USD-USDT",
    "NDX100": "NCSINASDAQ1002USD-USDT",
    "DOWJONES": "NCSIDOWJONES2USD-USDT",
    "NIKKEI225": "NCSINIKKEI2252USD-USDT",
    "RUSSELL2000": "NCSIRUSSELL20002USD-USDT",
    # Forex
    "USDJPY": "NCFXUSD2JPY-USDT",
    "EURUSD": "NCFXEUR2USD-USDT",
    "GBPUSD": "NCFXGBP2USD-USDT",
    # Commodities
    "GOLD": "NCCOGOLD2USD-USDT",
    "OIL_WTI": "NCCO724OILWTI2USD-USDT",
    "OIL_BRENT": "NCCO724OILBRENT2USD-USDT",
    # Stocks
    "TSLA": "NCSKTSLA2USD-USDT",
    "NVDA": "NCSKNVDA2USD-USDT",
    "AAPL": "NCSKAAPL2USD-USDT",
    "GOOGL": "NCSKGOOGL2USD-USDT",
    "META": "NCSKMETA2USD-USDT",
    "MSFT": "NCSKMSFT2USD-USDT",
    "AMZN": "NCSKAMZN2USD-USDT",
    "MSTR": "NCSKMSTR2USD-USDT",
}

# ─── Backtesting ─────────────────────────────────────────────────────────────
COMMISSION_RATE: float = 0.001         # 0.1% per side
SLIPPAGE_RATE: float = 0.0005         # 0.05% per side
INITIAL_CAPITAL: float = 10_000.0
