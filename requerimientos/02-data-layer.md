# Capa 1 — Datos

> Responsabilidad: adquirir, normalizar y almacenar datos OHLCV de Binance para todos los pares configurados.

---

## 1.1 Fuente de Datos

**Binance API v3** para datos históricos y WebSocket para streaming en tiempo real.

### Endpoints principales

| Propósito              | Endpoint                          | Método |
|------------------------|-----------------------------------|--------|
| Klines históricas      | `GET /api/v3/klines`              | REST   |
| Klines en tiempo real  | `wss://stream.binance.com/ws`     | WS     |
| Info del exchange       | `GET /api/v3/exchangeInfo`        | REST   |
| Precio actual          | `GET /api/v3/ticker/price`        | REST   |

### Rate Limits de Binance

| Tipo                | Límite                      | Estrategia                           |
|---------------------|-----------------------------|--------------------------------------|
| Requests por minuto | 1,200 req/min               | Rate limiter con backoff exponencial |
| Peso por minuto     | 6,000 peso/min              | Cada kline request pesa ~2           |
| Órdenes por día     | 160,000/día                 | No relevante en fase de datos        |

---

## 1.2 Formato OHLCV

Cada vela (candlestick) contiene:

```python
@dataclass
class Candle:
    timestamp: int        # Unix ms del open
    open: float
    high: float
    low: float
    close: float
    volume: float         # Volumen en base asset
    quote_volume: float   # Volumen en quote asset (USDT)
    trades: int           # Número de trades en la vela
    close_time: int       # Unix ms del cierre
```

### Respuesta raw de Binance `/api/v3/klines`

```
[
  [
    1499040000000,      // Open time
    "0.01634000",       // Open
    "0.80000000",       // High
    "0.01575800",       // Low
    "0.01577100",       // Close
    "148976.11427815",  // Volume
    1499644799999,      // Close time
    "2434.19055334",    // Quote asset volume
    308,                // Number of trades
    "1756.87402397",    // Taker buy base asset volume
    "28.46694368",      // Taker buy quote asset volume
    "17928899.62484339" // Ignore
  ]
]
```

---

## 1.3 Timeframes

El timeframe principal es **15 minutos** (`15m`), pero el sistema debe soportar cualquier intervalo para backtesting:

| Intervalo | Código Binance | Velas/día | Uso                          |
|-----------|----------------|-----------|------------------------------|
| 1 minuto  | `1m`           | 1,440     | Backtesting granular         |
| 5 minutos | `5m`           | 288       | Backtesting rápido           |
| 15 minutos| `15m`          | 96        | **Timeframe principal**      |
| 1 hora    | `1h`           | 24        | Contexto macro / filtro      |
| 4 horas   | `4h`           | 6         | Tendencia mayor              |

---

## 1.4 Pares Configurables

Los pares se definen en `config/pairs.py`. Criterios de selección recomendados:

- Volumen diario superior a $50M USD
- Spread bid-ask < 0.05%
- Disponibilidad en futuros (si se requiere short)

```python
# config/pairs.py
PAIRS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
]

TIMEFRAME = "15m"
```

---

## 1.5 Almacenamiento Local

Los datos descargados se almacenan localmente en archivos Parquet para evitar re-descargas y acelerar el backtesting.

```
data/
├── raw/
│   ├── BTCUSDT_15m.parquet
│   ├── ETHUSDT_15m.parquet
│   └── ...
└── processed/
    ├── BTCUSDT_15m_indicators.parquet
    └── ...
```

### ¿Por qué Parquet?

- Compresión columnar eficiente (10x más compacto que CSV)
- Lectura parcial de columnas (ideal para pandas)
- Tipos de datos preservados (sin parseo de strings)
- Compatible con `pandas.read_parquet()` directamente

---

## 1.6 Flujo de Datos

### Modo Backtesting (histórico)

```
Binance REST API
      │
      ▼
  fetcher.py        ← descarga lotes de 1000 velas
      │
      ▼
  storage.py         ← guarda en Parquet, detecta gaps
      │
      ▼
  DataFrame limpio   → pasa a Capa 2 (indicadores)
```

### Modo Live (tiempo real)

```
Binance WebSocket
      │
      ▼
  stream.py          ← recibe velas cada 15 min
      │
      ▼
  buffer circular    ← últimas N velas en memoria
      │
      ▼
  DataFrame live     → pasa a Capa 2 (indicadores)
```

---

## 1.7 Manejo de Errores

| Escenario                     | Acción                                          |
|-------------------------------|------------------------------------------------|
| Rate limit alcanzado          | Backoff exponencial: 1s → 2s → 4s → máx 60s   |
| WebSocket desconectado        | Reconexión automática + re-fetch de gap         |
| Datos incompletos (gaps)      | Log warning + relleno desde REST API            |
| Binance en mantenimiento      | Pausar bot, notificar, reintentar cada 5 min    |
| Timestamp duplicado           | Descartar duplicado, mantener el más reciente   |

---

## 1.8 Dependencias

```
python-binance>=1.0.19    # Cliente oficial de Binance
pandas>=2.0               # DataFrames
pyarrow>=14.0             # Lectura/escritura Parquet
websockets>=12.0          # WebSocket client (alternativa)
```
