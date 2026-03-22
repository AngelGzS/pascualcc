# Trading Bot - Divergence Confluence System

Bot de trading automatizado para Binance basado en deteccion de divergencias con confluence scoring.

## Requisitos

- Python >= 3.10
- Windows / Linux / macOS

## Instalacion

```bash
# Crear virtual environment
python -m venv .venv

# Activar (Windows)
.venv\Scripts\activate

# Activar (Linux/macOS)
source .venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt
```

## Configuracion

1. Copiar variables de entorno (opcional para backtesting):
```bash
set BINANCE_API_KEY=tu_api_key
set BINANCE_API_SECRET=tu_api_secret
```

2. Configurar pares en `config/pairs.py`
3. Ajustar parametros en `config/settings.py`

## Uso

### Descargar datos historicos

```bash
python main.py download --pairs BTCUSDT ETHUSDT --days 180
```

### Ejecutar backtest simple

```bash
python main.py backtest --pair BTCUSDT
```

### Walk-forward optimization

```bash
python main.py walk-forward --pair BTCUSDT --trials 200
```

## Estructura

```
config/     - Configuracion global y pares
data/       - Fetcher Binance, almacenamiento Parquet, WebSocket
indicators/ - RSI, MFI, TSI, ATR, EMA (calculados desde cero)
signals/    - Divergencias, BOS, motor de senales
scoring/    - Confluence score 0-100
execution/  - Entry trailing, stop loss, take profit
risk/       - Position sizing, kill switch, portfolio
backtest/   - Motor de backtest, walk-forward, metricas
tests/      - Tests unitarios
```

## Tests

```bash
pytest tests/ -v
```

## Arquitectura

6 capas con flujo unidireccional:

1. **Datos** - Binance API, OHLCV, Parquet
2. **Indicadores** - RSI, MFI, TSI, ATR, EMA
3. **Senales** - Divergencias regulares/ocultas, BOS
4. **Confluence** - Score 0-100 ponderado
5. **Ejecucion** - Entry trailing, SL/TP dinamico
6. **Riesgo** - 2% por trade, kill switch 15%
