# Trading Bot — Diseño de Sistema

> **Versión:** 1.0  
> **Fecha:** 2026-03-18  
> **Autor:** [Tu nombre]  
> **Estado:** Fase de diseño — pre-codificación

---

## 1. Resumen Ejecutivo

Bot de trading automatizado para Binance (spot + futuros) basado en detección de divergencias con confluence scoring. El sistema opera en múltiples pares simultáneamente en timeframe de 15 minutos, con arquitectura modular de 6 capas diseñada para backtesting riguroso antes de ejecución live.

| Campo                | Detalle                                              |
|----------------------|------------------------------------------------------|
| **Exchange**         | Binance (spot + futuros)                             |
| **Capital inicial**  | $500 USD                                             |
| **Lenguaje**         | Python                                               |
| **SO desarrollo**    | Windows                                              |
| **Timeframe base**   | 15 min (configurable para backtesting)               |
| **Pares**            | Múltiples simultáneos                                |
| **Estrategia core**  | Divergencias regulares/ocultas + confluence scoring  |
| **Riesgo por trade** | 2% del portafolio                                    |
| **Kill switch**      | 15% drawdown total                                   |

---

## 2. Filosofía del Proyecto

El desarrollo sigue un enfoque de validación progresiva donde cada fase debe completarse antes de avanzar a la siguiente:

1. **Diseñar primero, codificar después** — toda la lógica queda documentada antes de escribir una línea de código.
2. **Backtesting riguroso** — walk-forward testing, no solo in-sample. Si los resultados no son rentables, no se avanza.
3. **Paper trading** — validación en condiciones reales sin capital en juego.
4. **No escalar capital hasta validar el edge** — los $500 USD iniciales son para validar, no para buscar ganancias inmediatas.

---

## 3. Arquitectura en 6 Capas

El sistema se estructura en capas con responsabilidades claras y flujo de datos unidireccional de arriba hacia abajo:

```
┌─────────────────────────────────────────────────────┐
│  Capa 1 — DATOS                                     │
│  Binance API · OHLCV histórico y tiempo real        │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  Capa 2 — INDICADORES                               │
│  RSI · MFI · TSI · ATR · EMA 20/50/200             │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  Capa 3 — MOTOR DE SEÑALES                          │
│  Divergencia regular · Divergencia oculta · BOS     │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  Capa 4 — CONFLUENCE SCORE                          │
│  Combinación ponderada → score 0–100 → umbral       │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  Capa 5 — EJECUCIÓN                                 │
│  Entry trailing · Stop loss ATR · Take profit       │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  Capa 6 — RISK MANAGEMENT                           │
│  Kill switch · Tamaño posición · Exposición total   │
└─────────────────────────────────────────────────────┘
```

---

## 4. Orden de Construcción

| Paso | Capas        | Entregable                                        | Gate de avance                       |
|------|--------------|---------------------------------------------------|--------------------------------------|
| 1    | Capa 1 + 2   | Datos reales de Binance + indicadores calculados  | Indicadores validados manualmente    |
| 2    | Capa 3       | Motor de detección de divergencias                | Divergencias detectadas correctamente|
| 3    | Capa 4       | Confluence scorer                                 | Scoring funcional y coherente        |
| 4    | Backtesting  | Walk-forward completo                             | **Rentabilidad demostrada**          |
| 5    | Capa 5 + 6   | Ejecución live + risk management                  | Solo si paso 4 es positivo           |

---

## 5. Estructura de Archivos Propuesta

```
trading-bot/
├── config/
│   ├── settings.py          # Configuración global
│   └── pairs.py             # Definición de pares a operar
├── data/
│   ├── fetcher.py           # Binance API client
│   ├── storage.py           # Almacenamiento local de OHLCV
│   └── stream.py            # WebSocket para datos en tiempo real
├── indicators/
│   ├── rsi.py
│   ├── mfi.py
│   ├── tsi.py
│   ├── atr.py
│   ├── ema.py
│   └── calculator.py        # Orquestador de indicadores
├── signals/
│   ├── divergence.py        # Detección de divergencias
│   ├── structure.py         # BOS y contexto de tendencia
│   └── engine.py            # Motor de señales combinado
├── scoring/
│   └── confluence.py        # Confluence Score 0-100
├── execution/
│   ├── entry.py             # Entry trailing
│   ├── exit.py              # Stop loss + take profit
│   └── orders.py            # Interfaz con Binance para órdenes
├── risk/
│   ├── position_sizer.py    # Cálculo de tamaño de posición
│   ├── kill_switch.py       # Protección de drawdown
│   └── portfolio.py         # Exposición total
├── backtest/
│   ├── engine.py            # Motor de backtesting
│   ├── walk_forward.py      # Walk-forward optimizer
│   └── metrics.py           # Métricas de rendimiento
├── tests/
│   └── ...                  # Tests unitarios por módulo
├── main.py                  # Entry point
└── requirements.txt
```

---

## 6. Documentos de Detalle

Cada capa tiene su propia documentación técnica:

| Documento                  | Contenido                                                  |
|----------------------------|------------------------------------------------------------|
| `02-data-layer.md`         | API de Binance, formatos OHLCV, rate limits, WebSocket     |
| `03-indicators.md`         | Fórmulas, períodos, parámetros de cada indicador           |
| `04-signal-engine.md`      | Algoritmo de divergencias, BOS, detección de pivots        |
| `05-confluence-scoring.md` | Pesos, umbrales, sistema de puntuación                     |
| `06-execution.md`          | Entry trailing, stop loss dinámico, take profit            |
| `07-risk-management.md`    | Kill switch, position sizing, exposición                   |
| `08-backtesting.md`        | Walk-forward, métricas, criterios de validación            |
