# Capa 6 — Risk Management

> Responsabilidad: proteger el capital con límites absolutos de riesgo por trade, por portafolio y por drawdown total.

---

## 6.1 Principios de Riesgo

1. **Sobrevivir primero, ganar después** — el objetivo principal es no perder el capital.
2. **Riesgo fijo por trade** — nunca más del 2% del portafolio en una sola operación.
3. **Kill switch automático** — el bot se detiene si el drawdown total alcanza el 15%.
4. **Sin intervención emocional** — las reglas son mecánicas y se aplican siempre.

---

## 6.2 Position Sizing

### Fórmula

```
riesgo_por_trade = capital_actual × 0.02    (2%)
distancia_stop   = ATR × atr_multiplier
tamaño_posición  = riesgo_por_trade / distancia_stop
```

### Ejemplo con $500 USD

```
Capital: $500
Riesgo 2%: $10
ATR (BTC/USDT 15m): $150
Multiplicador ATR: 2.0
Distancia stop: $150 × 2.0 = $300

Tamaño posición = $10 / $300 = 0.0333 BTC
Valor posición = 0.0333 × $60,000 = ~$2,000

→ Con $500 esto requiere 4× apalancamiento en futuros
→ En spot, el tamaño se ajusta al capital disponible
```

### Restricciones

| Regla                               | Límite                                    |
|-------------------------------------|-------------------------------------------|
| Riesgo máx por trade                | 2% del capital actual                     |
| Tamaño máx en spot                  | 100% del capital disponible               |
| Apalancamiento máx en futuros       | Configurable (recomendado: máx 5×)        |
| Tamaño mínimo de orden              | Respetar `minQty` y `minNotional` Binance |

### Capital actual vs capital inicial

El tamaño de posición se calcula sobre el **capital actual**, no el inicial. Si el portafolio baja de $500 a $450, el 2% es $9, no $10. Esto reduce el riesgo automáticamente en rachas perdedoras.

---

## 6.3 Exposición Total del Portafolio

Con múltiples posiciones simultáneas, la exposición total debe estar controlada.

### Límites

| Métrica                             | Límite                                  |
|-------------------------------------|-----------------------------------------|
| Máximo posiciones abiertas          | Configurable (default: 3)               |
| Riesgo total abierto                | Máx 6% del portafolio (3 × 2%)         |
| Correlación                         | No más de 2 posiciones en pares altcoins|
| Exposición direccional              | No más de 4% net long o net short       |

### Control de correlación

Muchos altcoins se mueven en la misma dirección que BTC. Tener 3 longs simultáneos en altcoins es efectivamente una sola apuesta amplificada.

```python
def check_correlation(new_pair: str, open_positions: list) -> bool:
    """
    Reglas:
    - Si ya hay 2 posiciones long en altcoins, no abrir otro long altcoin
    - BTC y ETH se consideran "independientes" entre sí
    - El resto de altcoins se agrupan como correlacionados
    """
```

---

## 6.4 Kill Switch

Mecanismo de emergencia que detiene toda operación cuando las pérdidas alcanzan un nivel inaceptable.

### Configuración

```python
# config/settings.py
KILL_SWITCH_DRAWDOWN = 0.15   # 15% de drawdown desde peak
KILL_SWITCH_DAILY_LOSS = 0.05 # 5% de pérdida en un solo día (opcional)
```

### Lógica

```python
def check_kill_switch(portfolio: Portfolio) -> bool:
    """
    peak_equity = máximo valor del portafolio alcanzado
    current_equity = valor actual del portafolio
    drawdown = (peak_equity - current_equity) / peak_equity

    Si drawdown >= 0.15:
      1. Cerrar todas las posiciones abiertas inmediatamente
      2. Cancelar todas las órdenes pendientes
      3. Desactivar el bot
      4. Enviar notificación al operador
      5. Requiere intervención manual para reactivar
    """
```

### Escenarios del Kill Switch

| Escenario                    | Peak     | Actual   | Drawdown | Acción      |
|------------------------------|----------|----------|----------|-------------|
| Capital inicial sin ganancias| $500     | $430     | 14%      | Seguir      |
| Capital inicial sin ganancias| $500     | $425     | **15%**  | **KILL**    |
| Después de ganancias         | $620     | $527     | **15%**  | **KILL**    |
| Recuperación parcial         | $620     | $540     | 12.9%    | Seguir      |

---

## 6.5 Notificaciones

El sistema debe notificar al operador en eventos críticos:

| Evento                      | Canal           | Urgencia   |
|-----------------------------|-----------------|------------|
| Kill switch activado        | Telegram + email| CRÍTICA    |
| Drawdown > 10%              | Telegram        | Alta       |
| Trade ejecutado             | Telegram        | Normal     |
| WebSocket desconectado      | Telegram        | Alta       |
| Error de API Binance        | Log + Telegram  | Alta       |

### Implementación sugerida

```python
# Telegram Bot API (gratuito, tiempo real)
import requests

def notify_telegram(message: str, urgency: str = "normal"):
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    prefix = "🔴" if urgency == "critical" else "⚠️" if urgency == "high" else "ℹ️"
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": f"{prefix} {message}"}
    )
```

---

## 6.6 Logging y Auditoría

Cada decisión del sistema queda registrada para análisis post-mortem:

```
logs/
├── trades.csv              # Registro completo de trades
├── signals.csv             # Todas las señales generadas (incluso no operadas)
├── risk_events.csv         # Kill switch, límites alcanzados
├── errors.csv              # Errores de API, desconexiones
└── daily_summary.csv       # P&L diario, drawdown, métricas
```

### Métricas de monitoreo continuo

| Métrica                    | Cálculo                                         | Alerta si          |
|----------------------------|--------------------------------------------------|--------------------|
| Drawdown actual            | (peak - actual) / peak                          | > 10%              |
| Win rate (rolling 20)      | Trades ganadores / últimos 20 trades            | < 30%              |
| Avg R:R (rolling 20)       | Ganancia promedio / pérdida promedio             | < 1.0              |
| Trades por día             | Conteo de trades en 24h                         | > 10 (algo raro)   |
| Slippage promedio          | Precio ejecutado vs precio esperado             | > 0.1%             |
