# Capa 5 — Ejecución

> Responsabilidad: gestionar la entrada y salida de posiciones con entry trailing, stop loss dinámico basado en ATR y take profit por estructura.

---

## 5.1 Entry Trailing

En lugar de entrar inmediatamente cuando aparece una señal, el entry trailing **sigue el precio** para obtener un mejor punto de entrada.

### Lógica para Long

```
1. Señal long con score >= umbral detectada
2. Activar entry trailing:
   - entry_trigger = precio_actual - (ATR × entry_factor)
   - El trigger sube si el precio sube (trailing)
   - El trigger NUNCA baja
3. Entrar cuando el precio toque el trigger desde arriba
   (el precio retrocede hacia el trigger = mejor entrada)
4. Timeout: si no se activa en N velas, cancelar señal
```

### Lógica para Short

```
1. Señal short con score >= umbral detectada
2. Activar entry trailing:
   - entry_trigger = precio_actual + (ATR × entry_factor)
   - El trigger baja si el precio baja (trailing)
   - El trigger NUNCA sube
3. Entrar cuando el precio toque el trigger desde abajo
4. Timeout: si no se activa en N velas, cancelar señal
```

### Parámetros

| Parámetro        | Valor inicial | Rango backtest | Descripción                    |
|------------------|---------------|----------------|--------------------------------|
| `entry_factor`   | 0.5           | 0.2–1.0        | Multiplicador ATR para trigger |
| `entry_timeout`  | 8 velas       | 4–12           | Velas máx para activar entrada |

### Beneficios del Entry Trailing

- Evita entrar en el peor momento de un spike
- Obtiene precio de entrada más favorable
- Reduce el tamaño del stop loss efectivo
- Las señales falsas se auto-cancelan por timeout

---

## 5.2 Stop Loss Dinámico (ATR)

El stop loss no es un porcentaje fijo sino que se adapta a la volatilidad actual del mercado.

### Fórmula

```
LONG:
  stop_loss = precio_entrada - (ATR × atr_multiplier)

SHORT:
  stop_loss = precio_entrada + (ATR × atr_multiplier)
```

### Parámetros

| Parámetro          | Valor inicial | Rango backtest | Notas                         |
|--------------------|---------------|----------------|-------------------------------|
| `atr_multiplier`   | 2.0           | 1.0–3.0        | Balance entre ruido y riesgo  |

### Comportamiento

| Multiplicador | Efecto                                                  |
|---------------|---------------------------------------------------------|
| 1.0           | Stop muy ajustado, se activa frecuentemente por ruido   |
| 1.5           | Moderado, buen balance para mercados con tendencia      |
| 2.0           | Estándar, permite respirar al trade                     |
| 2.5           | Holgado, pocos stops prematuros pero mayor pérdida/trade|
| 3.0           | Muy holgado, solo para tendencias fuertes               |

### Trailing Stop (post-entrada)

Una vez en posición, el stop loss puede hacer trailing para proteger ganancias:

```
LONG:
  Nuevo stop = max(stop_actual, precio_actual - (ATR × atr_multiplier))
  → El stop solo sube, nunca baja

SHORT:
  Nuevo stop = min(stop_actual, precio_actual + (ATR × atr_multiplier))
  → El stop solo baja, nunca sube
```

**Activación del trailing stop**: solo después de que el trade esté en ganancia por al menos 1× ATR (evitar que el trailing se active prematuramente).

---

## 5.3 Take Profit

Dos mecanismos de take profit complementarios:

### Opción A: Por niveles de estructura

```
LONG:
  take_profit = último swing high detectado (pivots)
  Si no hay swing high claro → precio_entrada + (ATR × tp_multiplier)

SHORT:
  take_profit = último swing low detectado (pivots)
  Si no hay swing low claro → precio_entrada - (ATR × tp_multiplier)
```

### Opción B: Take profit parcial

```
1. Al llegar a 1.5× ATR de ganancia → cerrar 50% de la posición
2. Mover stop loss a breakeven en el 50% restante
3. Dejar correr el restante con trailing stop
```

### Parámetros

| Parámetro          | Valor inicial | Rango backtest | Descripción                       |
|--------------------|---------------|----------------|-----------------------------------|
| `tp_multiplier`    | 3.0           | 2.0–5.0        | Ratio ATR para TP fijo            |
| `partial_tp_ratio` | 0.5           | 0.3–0.7        | % de posición a cerrar en primer TP|
| `partial_tp_atr`   | 1.5           | 1.0–2.5        | ATR múltiplo para primer TP       |

---

## 5.4 Ciclo de Vida de una Posición

```
                    ┌──────────────┐
                    │ SEÑAL (Capa4)│
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
              ┌─────│ENTRY TRAILING│
              │     └──────┬───────┘
              │            ▼
          timeout   ┌──────────────┐
          cancelar  │   POSICIÓN   │
              │     │   ABIERTA    │
              ▼     └──┬───┬───┬───┘
           (nada)      │   │   │
                       ▼   ▼   ▼
                      SL   TP  Trailing
                       │   │   Stop
                       ▼   ▼   ▼
                    ┌──────────────┐
                    │   POSICIÓN   │
                    │   CERRADA    │
                    └──────────────┘
```

### Estados de una posición

```python
class PositionState(Enum):
    PENDING_ENTRY  = "pending_entry"    # Entry trailing activo
    OPEN           = "open"             # Posición abierta
    PARTIAL_TP     = "partial_tp"       # TP parcial ejecutado
    CLOSED_SL      = "closed_sl"        # Cerrada por stop loss
    CLOSED_TP      = "closed_tp"        # Cerrada por take profit
    CLOSED_TRAIL   = "closed_trail"     # Cerrada por trailing stop
    CANCELLED      = "cancelled"        # Entry trailing expiró
    KILLED         = "killed"           # Kill switch activado
```

---

## 5.5 Ejecución de Órdenes en Binance

### Tipo de órdenes a utilizar

| Momento          | Tipo de orden         | Razón                                |
|------------------|-----------------------|--------------------------------------|
| Entry            | LIMIT                 | Precio controlado por entry trailing |
| Stop loss        | STOP_MARKET           | Ejecución garantizada (slippage ok)  |
| Take profit      | LIMIT                 | Mejor precio posible                 |
| Trailing stop    | No nativo — manual    | Binance trailing stop no es flexible |

### Manejo de slippage

- En spot, usar `LIMIT` orders con margen de 0.05% sobre el precio deseado
- En futuros, `STOP_MARKET` es preferible para SL (garantiza ejecución)
- Log el slippage real para métricas de rendimiento

---

## 5.6 Registro de Trades

Cada trade se registra con toda la información para análisis posterior:

```python
@dataclass
class TradeRecord:
    trade_id: str
    pair: str
    direction: str
    confluence_score: int
    entry_price: float
    entry_time: int
    exit_price: float
    exit_time: int
    exit_reason: str          # 'stop_loss' | 'take_profit' | 'trailing' | 'kill_switch'
    position_size: float
    pnl_usd: float
    pnl_percent: float
    atr_at_entry: float
    atr_multiplier: float
    max_favorable_excursion: float   # Máxima ganancia durante el trade
    max_adverse_excursion: float     # Máxima pérdida durante el trade
    duration_candles: int
```
