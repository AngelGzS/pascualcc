# Backtesting — Walk-Forward

> Responsabilidad: validar la estrategia con datos históricos de forma rigurosa, evitando overfitting y asegurando que el edge es real antes de arriesgar capital.

---

## 8.1 ¿Por Qué Walk-Forward?

El backtesting simple (optimizar sobre todo el dataset) produce resultados engañosos porque el sistema se ajusta al pasado sin capacidad de predecir el futuro. Esto se llama **overfitting**.

Walk-forward testing resuelve esto dividiendo los datos en segmentos temporales y validando que los parámetros optimizados en un período funcionen en el siguiente período no visto.

```
Backtesting simple (MALO):
  ┌─────────────────────────────────────┐
  │   Optimizar sobre TODO el dataset   │ → Overfitting garantizado
  └─────────────────────────────────────┘

Walk-forward (CORRECTO):
  ┌──────────┬────┐┌──────────┬────┐┌──────────┬────┐
  │ Entrenar │Test││ Entrenar │Test││ Entrenar │Test│
  │ (in-sam) │(OS)││ (in-sam) │(OS)││ (in-sam) │(OS)│
  └──────────┴────┘└──────────┴────┘└──────────┴────┘
   Ventana 1         Ventana 2         Ventana 3
```

---

## 8.2 Configuración Walk-Forward

### División de datos

| Parámetro                    | Valor                    | Notas                           |
|------------------------------|--------------------------|---------------------------------|
| Datos totales requeridos     | Mínimo 6 meses (15m)    | ~17,280 velas                   |
| Ventana in-sample            | 2 meses                 | Período de optimización         |
| Ventana out-of-sample        | 1 mes                   | Período de validación           |
| Overlap                      | 0                        | Sin superposición               |
| Ventanas totales             | ~4-6 dependiendo de data | Más = más confiable             |

### Ejemplo con 6 meses de datos

```
Mes 1   Mes 2   Mes 3   Mes 4   Mes 5   Mes 6
├───────────────┤───────┤
  Train (in)       Test (out)     ← Ventana 1

        ├───────────────┤───────┤
          Train (in)       Test   ← Ventana 2

                ├───────────────┤───────┤
                  Train (in)       Test   ← Ventana 3
```

---

## 8.3 Parámetros a Optimizar

Estos son los parámetros que se optimizan en cada ventana in-sample:

| Parámetro                 | Rango          | Step   | Combinaciones |
|---------------------------|----------------|--------|---------------|
| `atr_multiplier`          | 1.0 – 3.0     | 0.25   | 9             |
| `confluence_threshold`    | 40 – 75        | 5      | 8             |
| `entry_factor`            | 0.2 – 1.0     | 0.2    | 5             |
| `entry_timeout`           | 4 – 12         | 2      | 5             |
| `pivot_left`              | 3 – 7          | 1      | 5             |
| `pivot_right`             | 3 – 7          | 1      | 5             |
| `tp_multiplier`           | 2.0 – 5.0     | 0.5    | 7             |

**Total combinaciones**: 9 × 8 × 5 × 5 × 5 × 5 × 7 = **315,000**

Esto es manejable con grid search para un solo par. Para múltiples pares, considerar optimización bayesiana con Optuna.

---

## 8.4 Métrica de Optimización

No optimizar solo por profit total. La métrica principal debe balancear rendimiento y riesgo:

### Métrica primaria: Calmar Ratio modificado

```
calmar_ratio = rendimiento_anualizado / máximo_drawdown

Ejemplo:
  Rendimiento mensual: 5%
  Rendimiento anualizado: ~80%
  Máx drawdown: 12%
  Calmar ratio: 80/12 = 6.67
```

### Métricas secundarias (filtros)

| Métrica               | Mínimo aceptable | Razón                              |
|-----------------------|-------------------|------------------------------------|
| Total trades          | ≥ 30              | Significancia estadística mínima   |
| Win rate              | ≥ 35%             | Viable con buen R:R                |
| Profit factor         | ≥ 1.3             | Ganancias > pérdidas               |
| Max drawdown          | ≤ 15%             | Consistente con kill switch        |
| Avg R:R               | ≥ 1.5             | Compensar win rate                 |
| Sharpe ratio          | ≥ 1.0             | Retorno ajustado por riesgo        |

---

## 8.5 Criterios de Validación (Go/No-Go)

Una estrategia pasa la validación si **todas** estas condiciones se cumplen en el período out-of-sample:

```
✅ Calmar ratio out-of-sample > 50% del Calmar ratio in-sample
✅ Profit factor out-of-sample > 1.0 (rentable, aunque sea poco)
✅ Máximo drawdown out-of-sample ≤ 20%
✅ Al menos 70% de las ventanas out-of-sample son rentables
✅ No hay ventana out-of-sample con pérdida > 10%
```

**Si alguna condición falla** → la estrategia no tiene edge demostrable → no pasar a live.

---

## 8.6 Walk-Forward Efficiency (WFE)

Mide qué tan bien los resultados in-sample predicen los resultados out-of-sample:

```
WFE = rendimiento_out_of_sample / rendimiento_in_sample × 100

WFE > 50%:  Buena estabilidad, parámetros robustos
WFE 30-50%: Aceptable, proceder con cautela
WFE < 30%:  Overfitting probable, no operar
```

---

## 8.7 Supuestos Realistas del Backtest

El backtest debe incluir costos reales para evitar resultados inflados:

| Costo                   | Valor                     | Notas                           |
|-------------------------|---------------------------|---------------------------------|
| Comisión por trade      | 0.1% maker / 0.1% taker  | Binance tier base               |
| Slippage estimado       | 0.05%                     | Adicional por ejecución real    |
| Funding rate (futuros)  | Variable, ~0.01%/8h       | Solo para posiciones overnight  |
| Costo total por trade   | ~0.25% roundtrip          | Entrada + salida                |

### Cosas que el backtest NO captura

- Liquidez insuficiente para el tamaño de orden
- Latencia de red
- Downtime del exchange
- Manipulación de mercado (wicks anómalos)
- Cambios de régimen de mercado (bull → bear)

---

## 8.8 Reporte de Backtesting

Cada ejecución de backtest genera un reporte completo:

```
=== BACKTEST REPORT ===
Par: BTCUSDT | Timeframe: 15m
Período: 2025-07-01 a 2026-01-01 (6 meses)
Walk-forward: 3 ventanas (2m train / 1m test)

--- Resultados Out-of-Sample Agregados ---
Total trades:       87
Win rate:           42.5%
Profit factor:      1.65
Avg R:R:            2.1:1
Max drawdown:       11.3%
Calmar ratio:       5.8
Sharpe ratio:       1.4
WFE:                62%

--- Por Ventana ---
Ventana 1 (OOS): +4.2% | 28 trades | WR 39% | MDD 8.1%
Ventana 2 (OOS): +6.1% | 31 trades | WR 45% | MDD 6.7%
Ventana 3 (OOS): +3.8% | 28 trades | WR 43% | MDD 11.3%

--- Parámetros Óptimos (última ventana) ---
atr_multiplier: 2.0
confluence_threshold: 55
entry_factor: 0.4
tp_multiplier: 3.5
pivot_left: 5
pivot_right: 5

--- VEREDICTO: ✅ GO (todos los criterios cumplidos) ---
```

---

## 8.9 Después del Backtesting

Si el backtest pasa → el siguiente paso es **paper trading** (simulación en tiempo real sin dinero):

| Fase              | Duración mínima | Criterio de avance                         |
|-------------------|------------------|--------------------------------------------|
| Paper trading     | 2–4 semanas      | Resultados consistentes con backtest       |
| Live con mínimo   | 1 mes            | Operar con $100–200, no los $500 completos |
| Live completo     | Indefinido       | Solo si las fases anteriores son positivas |
