# Capa 2 — Indicadores

> Responsabilidad: calcular todos los indicadores técnicos a partir de los datos OHLCV limpios.

---

## 2.1 Indicadores del Sistema

| Indicador | Tipo       | Período  | Rol en el sistema                        |
|-----------|------------|----------|------------------------------------------|
| RSI       | Momentum   | 14       | Divergencias + confluence                |
| MFI       | Momentum   | 14       | Divergencias con volumen + confluence    |
| TSI       | Momentum   | 25/13    | Momentum suavizado + confluence          |
| ATR       | Volatilidad| 14       | Stop loss dinámico + filtro volatilidad  |
| EMA 20    | Tendencia  | 20       | Tendencia corto plazo                    |
| EMA 50    | Tendencia  | 50       | Tendencia medio plazo                    |
| EMA 200   | Tendencia  | 200      | Tendencia largo plazo / filtro macro     |

---

## 2.2 RSI — Relative Strength Index

Mide la velocidad y magnitud de los cambios de precio. No se usa como señal de sobreventa/sobrecompra directa sino para detectar **divergencias** con el precio.

### Fórmula

```
cambio = close[i] - close[i-1]

ganancia = cambio si cambio > 0, sino 0
pérdida  = |cambio| si cambio < 0, sino 0

avg_gain = EMA(ganancia, período=14)
avg_loss = EMA(pérdida, período=14)

RS  = avg_gain / avg_loss
RSI = 100 - (100 / (1 + RS))
```

### Parámetros

| Parámetro  | Valor  | Notas                                              |
|------------|--------|----------------------------------------------------|
| Período    | 14     | Estándar de Wilder                                 |
| Método     | EMA    | Suavizado exponencial (no SMA)                     |
| Rango      | 0–100  | Pero NO usamos 30/70 como señal directa            |

### Uso en el sistema

- **Capa 3**: detección de divergencias entre precio y RSI
- **Capa 4**: componente del confluence score

---

## 2.3 MFI — Money Flow Index

Similar al RSI pero incorpora volumen, lo que le da una dimensión adicional de confirmación. Un "RSI ponderado por volumen".

### Fórmula

```
typical_price = (high + low + close) / 3
raw_money_flow = typical_price × volume

Si typical_price[i] > typical_price[i-1]:
    positive_flow += raw_money_flow
Si typical_price[i] < typical_price[i-1]:
    negative_flow += raw_money_flow

money_ratio = sum(positive_flow, 14) / sum(negative_flow, 14)
MFI = 100 - (100 / (1 + money_ratio))
```

### Parámetros

| Parámetro  | Valor | Notas                                    |
|------------|-------|------------------------------------------|
| Período    | 14    | Consistente con RSI                      |
| Rango      | 0–100 | Incorpora volumen (ventaja vs RSI puro)  |

### Uso en el sistema

- **Capa 3**: divergencias MFI-precio (confirmación con volumen)
- **Capa 4**: componente del confluence score — pesa más que RSI por incluir volumen

---

## 2.4 TSI — True Strength Index

Momentum suavizado doblemente que filtra ruido del mercado. Menos sensible a movimientos erráticos que RSI.

### Fórmula

```
momentum = close[i] - close[i-1]

double_smoothed_momentum = EMA(EMA(momentum, 25), 13)
double_smoothed_abs      = EMA(EMA(|momentum|, 25), 13)

TSI = 100 × (double_smoothed_momentum / double_smoothed_abs)

signal_line = EMA(TSI, 7)
```

### Parámetros

| Parámetro       | Valor | Notas                                |
|-----------------|-------|--------------------------------------|
| Long period     | 25    | Primer suavizado                     |
| Short period    | 13    | Segundo suavizado                    |
| Signal period   | 7     | Línea de señal                       |
| Rango           | -100 a +100 | Centrado en cero                |

### Uso en el sistema

- **Capa 3**: divergencias TSI-precio
- **Capa 4**: componente del confluence score — el cruce TSI/signal aporta puntos extra

---

## 2.5 ATR — Average True Range

Mide la volatilidad del mercado. No genera señales de dirección sino que dimensiona el riesgo.

### Fórmula

```
true_range = max(
    high - low,
    |high - close[i-1]|,
    |low - close[i-1]|
)

ATR = EMA(true_range, período=14)
```

### Parámetros

| Parámetro  | Valor | Notas                                       |
|------------|-------|---------------------------------------------|
| Período    | 14    | Estándar                                    |
| Método     | EMA   | Más reactivo que SMA para volatilidad       |

### Uso en el sistema

- **Capa 5**: stop loss dinámico = `precio_entrada - (ATR × multiplicador)`
- **Capa 5**: filtro — no operar si ATR es extremadamente bajo (mercado sin movimiento)
- **Capa 6**: tamaño de posición = `(capital × 2%) / (ATR × multiplicador)`
- **Capa 4**: componente del confluence score — volatilidad adecuada suma puntos

---

## 2.6 EMA — Exponential Moving Average

Tres EMAs para definir el contexto de tendencia y filtrar señales contrarias.

### Fórmula

```
multiplier = 2 / (período + 1)
EMA[i] = (close[i] × multiplier) + (EMA[i-1] × (1 - multiplier))
```

### Configuración de las tres EMAs

| EMA   | Período | Rol                                               |
|-------|---------|----------------------------------------------------|
| EMA 20  | 20    | Tendencia de corto plazo / momentum inmediato      |
| EMA 50  | 50    | Tendencia de medio plazo / soporte/resistencia     |
| EMA 200 | 200   | Tendencia macro / filtro direccional principal      |

### Señales de contexto de tendencia

| Condición                          | Contexto                |
|------------------------------------|-------------------------|
| Precio > EMA20 > EMA50 > EMA200   | Tendencia alcista fuerte|
| Precio < EMA20 < EMA50 < EMA200   | Tendencia bajista fuerte|
| EMAs entrelazadas                  | Rango / sin tendencia   |

### Uso en el sistema

- **Capa 3**: contexto de tendencia para validar BOS
- **Capa 4**: alineación de EMAs suma puntos al confluence score
- **Capa 5**: filtro — no tomar longs si precio < EMA200 (configurable)

---

## 2.7 Pipeline de Cálculo

Todos los indicadores se calculan en un solo paso sobre el DataFrame para eficiencia:

```python
def calculate_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Input:  DataFrame con columnas [timestamp, open, high, low, close, volume]
    Output: DataFrame original + columnas de indicadores añadidas
    """
    df['rsi']     = calc_rsi(df['close'], period=14)
    df['mfi']     = calc_mfi(df, period=14)
    df['tsi']     = calc_tsi(df['close'], long=25, short=13)
    df['tsi_signal'] = calc_ema(df['tsi'], period=7)
    df['atr']     = calc_atr(df, period=14)
    df['ema_20']  = calc_ema(df['close'], period=20)
    df['ema_50']  = calc_ema(df['close'], period=50)
    df['ema_200'] = calc_ema(df['close'], period=200)
    return df
```

### Período de warmup

Los indicadores necesitan datos previos para estabilizarse. El warmup mínimo es dictado por el indicador más lento:

| Indicador | Warmup mínimo  |
|-----------|----------------|
| RSI 14    | ~50 velas      |
| MFI 14    | ~50 velas      |
| TSI 25/13 | ~80 velas      |
| ATR 14    | ~30 velas      |
| EMA 200   | **~600 velas** |

**Warmup del sistema: 600 velas mínimo** (≈ 6.25 días en 15m) antes de generar cualquier señal.

---

## 2.8 Validación de Indicadores

Antes de avanzar a Capa 3, cada indicador se valida contra:

1. **TradingView** — comparar valores calculados vs TV para el mismo par/timeframe/timestamp
2. **Valores límite** — RSI y MFI siempre entre 0-100, TSI entre -100 y +100
3. **Consistencia** — mismos datos de entrada siempre producen misma salida (determinismo)
4. **NaN handling** — las primeras N velas (warmup) son NaN, nunca se usan para señales
