# Capa 4 — Confluence Score

> Responsabilidad: combinar todas las señales individuales en un score numérico 0–100. El bot solo entra si el score supera un umbral mínimo configurable.

---

## 4.1 Concepto

El Confluence Score responde a la pregunta: **¿qué tan fuerte es esta señal considerando toda la evidencia disponible?**

No todas las divergencias son iguales. Una divergencia RSI sola es débil, pero si coincide con divergencia MFI, BOS confirmado, EMAs alineadas y volatilidad adecuada, la probabilidad de éxito aumenta significativamente.

```
Score 0–30:   No operar (señal débil)
Score 31–50:  Marginal (operar solo con tamaño reducido, si se habilita)
Score 51–70:  Buena señal (operar con tamaño estándar)
Score 71–100: Señal fuerte (operar con confianza, tamaño completo)
```

---

## 4.2 Componentes del Score

| Componente                | Puntos máx. | Descripción                                         |
|---------------------------|-------------|-----------------------------------------------------|
| Divergencias detectadas   | 30          | Cuántos indicadores confirman la divergencia        |
| Tipo de divergencia       | 10          | Regular vs oculta, en contexto                      |
| BOS confirmado            | 15          | Break of Structure en la misma dirección            |
| Alineación de EMAs        | 15          | Las 3 EMAs alineadas con la dirección de la señal   |
| Contexto de tendencia     | 15          | Señal a favor de la tendencia mayor                 |
| Filtro de volatilidad     | 10          | ATR en rango óptimo (no extremo alto ni bajo)       |
| TSI cruce de señal        | 5           | TSI cruzando su signal line en dirección correcta   |
| **Total**                 | **100**     |                                                     |

---

## 4.3 Cálculo Detallado

### Divergencias detectadas (máx 30 pts)

```python
def score_divergences(signal: Signal) -> int:
    pts = 0
    n_indicators = len(signal.divergence_indicators)

    if n_indicators >= 1: pts += 10   # Al menos un indicador
    if n_indicators >= 2: pts += 10   # Dos indicadores confirman
    if n_indicators >= 3: pts += 10   # Los tres confirman (raro, muy fuerte)

    return pts
```

### Tipo de divergencia (máx 10 pts)

```python
def score_divergence_type(signal: Signal) -> int:
    # Divergencia regular en contexto de rango/transición → más valiosa
    # Divergencia oculta en contexto de tendencia establecida → más valiosa
    if signal.signal_type.startswith('regular') and signal.trend_context == 'neutral':
        return 10  # Reversión en punto de giro potencial
    if signal.signal_type.startswith('hidden') and signal.trend_context != 'neutral':
        return 10  # Continuación con tendencia confirmada
    if signal.signal_type.startswith('regular'):
        return 6   # Reversión pero no en punto ideal
    if signal.signal_type.startswith('hidden'):
        return 6   # Continuación pero sin tendencia clara
    return 0
```

### BOS confirmado (máx 15 pts)

```python
def score_bos(signal: Signal) -> int:
    if signal.bos_confirmed:
        return 15  # Estructura de mercado respalda la señal
    return 0
```

### Alineación de EMAs (máx 15 pts)

```python
def score_ema_alignment(signal: Signal) -> int:
    if signal.ema_alignment == 'aligned':
        return 15  # EMA20 > EMA50 > EMA200 (long) o inverso (short)
    if signal.ema_alignment == 'partial':
        return 8   # Al menos 2 de 3 EMAs alineadas
    return 0       # EMAs en contra o entrelazadas
```

### Contexto de tendencia (máx 15 pts)

```python
def score_trend_context(signal: Signal) -> int:
    # Señal long en tendencia alcista → máximo
    # Señal long en tendencia bajista → 0 (contratendencia)
    if signal.direction == 'long' and signal.trend_context == 'bullish':
        return 15
    if signal.direction == 'short' and signal.trend_context == 'bearish':
        return 15
    if signal.trend_context == 'neutral':
        return 7   # Sin tendencia clara, puntaje parcial
    return 0       # Contratendencia
```

### Filtro de volatilidad (máx 10 pts)

```python
def score_volatility(signal: Signal, atr_percentile: float) -> int:
    # ATR ideal: entre percentil 25 y 75 del histórico
    # Muy bajo → mercado muerto, sin movimiento
    # Muy alto → caos, stops se activan prematuramente
    if 25 <= atr_percentile <= 75:
        return 10
    if 15 <= atr_percentile <= 85:
        return 5
    return 0
```

### TSI cruce de señal (máx 5 pts)

```python
def score_tsi_cross(signal: Signal, tsi: float, tsi_signal: float,
                    prev_tsi: float, prev_tsi_signal: float) -> int:
    if signal.direction == 'long':
        if prev_tsi <= prev_tsi_signal and tsi > tsi_signal:
            return 5  # TSI cruzó al alza su signal line
    if signal.direction == 'short':
        if prev_tsi >= prev_tsi_signal and tsi < tsi_signal:
            return 5  # TSI cruzó a la baja su signal line
    return 0
```

---

## 4.4 Umbral Mínimo

```python
# config/settings.py
CONFLUENCE_THRESHOLD = 55  # Score mínimo para operar (a optimizar en backtesting)
```

| Rango del umbral | Efecto                                                |
|-------------------|------------------------------------------------------|
| 40–50            | Más trades, menor selectividad, mayor drawdown        |
| 50–60            | Balance selectividad/frecuencia (punto de partida)    |
| 60–70            | Muy selectivo, pocos trades pero alta calidad         |
| 70+              | Ultra selectivo, casi no opera                        |

**El umbral óptimo se determina en backtesting (Paso 4)**, no se fija arbitrariamente.

---

## 4.5 Salida de Capa 4

```python
@dataclass
class ScoredSignal:
    signal: Signal              # La señal cruda de Capa 3
    confluence_score: int       # 0–100
    score_breakdown: dict       # Desglose por componente
    should_trade: bool          # score >= CONFLUENCE_THRESHOLD
    confidence: str             # 'weak' | 'moderate' | 'strong'
```

Si `should_trade == True`, la señal avanza a **Capa 5 (Ejecución)**.

---

## 4.6 Parámetros Optimizables en Backtesting

| Parámetro                       | Rango a probar | Default |
|---------------------------------|----------------|---------|
| `CONFLUENCE_THRESHOLD`          | 40–75          | 55      |
| Peso de divergencias            | 20–40          | 30      |
| Peso de BOS                     | 10–20          | 15      |
| Peso de EMAs                    | 10–20          | 15      |
| Peso de tendencia               | 10–20          | 15      |
| Peso de volatilidad             | 5–15           | 10      |
| Percentil ATR óptimo            | 20–80          | 25–75   |

Los pesos siempre deben sumar 100.
