"""Configuracion global centralizada. Todos los parametros numericos del sistema."""
from __future__ import annotations

import os

# Load .env file if it exists
from pathlib import Path

_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip())

# ─── Binance API ────────────────────────────────────────────────────────────
BINANCE_API_KEY: str = os.environ.get("BINANCE_API_KEY", "")
BINANCE_API_SECRET: str = os.environ.get("BINANCE_API_SECRET", "")
BINANCE_BASE_URL: str = "https://api.binance.com"
BINANCE_WS_URL: str = "wss://stream.binance.com/ws"

# Rate limiting
MAX_REQUESTS_PER_MINUTE: int = 1200
MAX_WEIGHT_PER_MINUTE: int = 6000
KLINE_REQUEST_WEIGHT: int = 2
BACKOFF_BASE_SECONDS: float = 1.0
BACKOFF_MAX_SECONDS: float = 60.0

# ─── Timeframe ──────────────────────────────────────────────────────────────
DEFAULT_TIMEFRAME: str = "15m"
KLINE_LIMIT: int = 1000  # Max velas per request

# ─── Data storage ───────────────────────────────────────────────────────────
DATA_DIR: str = "data"
RAW_DIR: str = os.path.join(DATA_DIR, "raw")
PROCESSED_DIR: str = os.path.join(DATA_DIR, "processed")

# ─── Indicators ─────────────────────────────────────────────────────────────
RSI_PERIOD: int = 14
MFI_PERIOD: int = 14
TSI_LONG_PERIOD: int = 25
TSI_SHORT_PERIOD: int = 13
TSI_SIGNAL_PERIOD: int = 7
ATR_PERIOD: int = 14
EMA_SHORT: int = 20
EMA_MID: int = 50
EMA_LONG: int = 200

# Warmup minimo (dictado por EMA 200)
WARMUP_CANDLES: int = 600

# ─── Pivots ─────────────────────────────────────────────────────────────────
PIVOT_LEFT: int = 5
PIVOT_RIGHT: int = 5

# ─── Divergence filters ────────────────────────────────────────────────────
MIN_PIVOT_DISTANCE: int = 5   # Minimo velas entre pivots
MAX_PIVOT_DISTANCE: int = 50  # Maximo velas entre pivots
SIGNAL_COOLDOWN: int = 3      # No repetir misma senal en < N velas

# ─── ATR percentile filter (pre-confluence) ─────────────────────────────────
ATR_LOW_PERCENTILE: float = 10.0  # Percentil minimo para operar

# ─── Confluence scoring ─────────────────────────────────────────────────────
CONFLUENCE_THRESHOLD: int = 55

# Weights (must sum to 100)
WEIGHT_DIVERGENCES: int = 30
WEIGHT_DIVERGENCE_TYPE: int = 10
WEIGHT_BOS: int = 15
WEIGHT_EMA_ALIGNMENT: int = 15
WEIGHT_TREND_CONTEXT: int = 15
WEIGHT_VOLATILITY: int = 10
WEIGHT_TSI_CROSS: int = 5

# Volatility scoring percentiles
VOLATILITY_OPTIMAL_LOW: float = 25.0
VOLATILITY_OPTIMAL_HIGH: float = 75.0
VOLATILITY_ACCEPTABLE_LOW: float = 15.0
VOLATILITY_ACCEPTABLE_HIGH: float = 85.0

# ─── Execution ──────────────────────────────────────────────────────────────
ENTRY_FACTOR: float = 0.5       # ATR multiplier for entry trailing trigger
ENTRY_TIMEOUT: int = 8          # Max candles to wait for entry
ATR_MULTIPLIER: float = 2.0     # Stop loss ATR multiplier
TP_MULTIPLIER: float = 3.0      # Take profit ATR multiplier
PARTIAL_TP_RATIO: float = 0.5   # % of position to close at first TP
PARTIAL_TP_ATR: float = 1.5     # ATR multiple for first TP
TRAILING_ACTIVATION_ATR: float = 1.0  # Activate trailing after 1x ATR profit

# ─── Risk management ───────────────────────────────────────────────────────
INITIAL_CAPITAL: float = 500.0
RISK_PER_TRADE: float = 0.02      # 2%
MAX_OPEN_POSITIONS: int = 3
MAX_TOTAL_RISK: float = 0.06      # 6% (3 x 2%)
MAX_ALTCOIN_SAME_DIR: int = 2     # Max same-direction altcoin positions
MAX_DIRECTIONAL_EXPOSURE: float = 0.04  # 4% net long or short
MAX_LEVERAGE: float = 5.0         # Futuros
KILL_SWITCH_DRAWDOWN: float = 0.15    # 15% from peak
KILL_SWITCH_DAILY_LOSS: float = 0.05  # 5% daily

# Independent pairs (not correlated with generic altcoins)
INDEPENDENT_PAIRS: list[str] = ["BTCUSDT", "ETHUSDT"]

# ─── Backtesting ────────────────────────────────────────────────────────────
COMMISSION_RATE: float = 0.001    # 0.1% per side
SLIPPAGE_RATE: float = 0.0005    # 0.05% per side
TOTAL_COST_PER_SIDE: float = COMMISSION_RATE + SLIPPAGE_RATE  # 0.15%

# Walk-forward
WF_IN_SAMPLE_DAYS: int = 120     # 4 months (more IS data → better optimization)
WF_OUT_SAMPLE_DAYS: int = 60     # 2 months (more OOS trades per window → stable WFE)
WF_MIN_TRADES: int = 10          # Lowered for 1H compatibility (fewer candles per window)
WF_MIN_WIN_RATE: float = 0.35
WF_MIN_PROFIT_FACTOR: float = 1.3
WF_MAX_DRAWDOWN: float = 0.15
WF_MIN_AVG_RR: float = 1.5
WF_MIN_SHARPE: float = 1.0

# Go/No-Go criteria
CALMAR_RATIO_DECAY: float = 0.35  # OOS Calmar must be > 35% of IS (cap=10 → threshold=3.5)
WFE_MIN: float = 0.05            # Walk-forward efficiency minimum (final: IS inflation is structural)

# Optuna
OPTUNA_N_TRIALS: int = 200       # Bayesian optimization trials

# ─── Paper Trading ─────────────────────────────────────────────────────────
PAPER_STATE_DIR: str = "data/paper"

# ─── Logging ────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
LOG_DIR: str = "logs"

# ─── Telegram (optional) ───────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")

# ─── Telegram API (copy trading) ──────────────────────────────────────────
TELEGRAM_API_ID: str = os.environ.get("TELEGRAM_API_ID", "")
TELEGRAM_API_HASH: str = os.environ.get("TELEGRAM_API_HASH", "")
TELEGRAM_SESSION_NAME: str = "pascualcc_session"
COPY_STATE_DIR: str = "data/copy"

# ─── BingX API (fallback exchange) ───────────────────────────────────────
BINGX_API_KEY: str = os.environ.get("BINGX_API_KEY", "")
BINGX_API_SECRET: str = os.environ.get("BINGX_API_SECRET", "")
BINGX_BASE_URL: str = os.environ.get("BINGX_BASE_URL", "https://open-api.bingx.com")
BINGX_WS_URL: str = os.environ.get("BINGX_WS_URL", "wss://open-api-ws.bingx.com/swap-market")
BINGX_MAX_REQUESTS_PER_MINUTE: int = 500

# ─── Price source for paper trading ─────────────────────────────────────
# "bingx" (default, works globally) or "binance" (may fail on US servers)
PRICE_SOURCE: str = os.environ.get("PRICE_SOURCE", "bingx").lower()
