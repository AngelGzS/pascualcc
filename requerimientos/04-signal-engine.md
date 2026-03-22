# Capa 3 — Motor de Señales

> Responsabilidad: detectar divergencias regulares y ocultas entre precio e indicadores, identificar estructura de mercado (BOS) y generar señales direccionales crudas.

---

## 3.1 Tipos de Divergencias

El corazón del sistema. Una divergencia ocurre cuando el precio y un indicador de momentum se mueven en direcciones opuestas, sugiriendo un cambio potencial o continuación.

### Divergencia Regular (reversal)

| Tipo              | Precio                  | Indicador               | Señal     |
|-------------------|-------------------------|-------------------------|-----------|
| Regular bullish   | Lower low               | Higher low              | Long      |
| Regular bearish   | Higher high             | Lower high              | Short     |

**Interpretación**: el momentum no confirma el movimiento del precio → posible agotamiento y reversal.

### Divergencia Oculta (continuación)

| Tipo             | Precio                  | Indicador               | Señal     |
|------------------|-------------------------|-------------------------|-----------|
| Oculta bullish   | Higher low              | Lower low               | Long      |
| Oculta bearish   | Lower high              | Higher high             | Short     |

**Interpretación**: el precio mantiene la estructura de tendencia pero el indicador sugiere pullback completado → continuación.

---

## 3.2 Detección de Pivots

Para identificar divergencias necesitamos encontrar los pivots (máximos y mínimos locales) tanto en precio como en los indicadores.

### Algoritmo de Pivot Detection

```python
def find_pivots(series: pd.Series, left: int = 5, right: int = 5) -> pd.DataFrame:
    """
    Un pivot high ocurre cuando series[i] es el máximo en la ventana
    [i-left, i+right].
    Un pivot low ocurre cuando series[i] es el mínimo en la ventana
    [i-left, i+right].

    Params:
        left:  velas a la izquierda para confirmar
        right: velas a la derecha para confirmar (introduce lag)

    Returns:
        DataFrame con columnas [pivot_high, pivot_low] (NaN donde no hay pivot)
    """
```

### Parámetros de pivots

| Parámetro  | Valor | Efecto                                           |
|------------|-------|--------------------------------------------------|
| `left`     | 5     | Más alto = pivots más significativos, menos ruido|
| `right`    | 5     | Introduce lag de 5 velas (75 min en 15m)         |

**Trade-off**: `left/right` más grandes producen pivots más confiables pero con mayor lag. Se optimizarán en backtesting entre 3-7.

---

## 3.3 Algoritmo de Detección de Divergencias

```
Para cada nuevo pivot detectado en precio:

1. Identificar el pivot anterior del mismo tipo (high-high o low-low)
2. Obtener los valores del indicador en esos mismos timestamps
3. Comparar las pendientes:

   precio_slope     = pivot_actual - pivot_anterior
   indicator_slope  = indicador_en_actual - indicador_en_anterior

4. Clasificar:
   Si precio_slope < 0 AND indicator_slope > 0 → Regular bullish
   Si precio_slope > 0 AND indicator_slope < 0 → Regular bearish
   Si precio_slope > 0 AND indicator_slope < 0 → Oculta bullish (en lows)
   Si precio_slope < 0 AND indicator_slope > 0 → Oculta bearish (en highs)

5. Filtros de calidad:
   - Distancia entre pivots: mínimo 5 velas, máximo 50 velas
   - Diferencia mínima de precio entre pivots (evitar micro-divergencias)
   - El indicador debe estar en zona relevante (no neutro)
```

### Indicadores usados para divergencias

Cada indicador se evalúa independientemente:

| Indicador | Divergencias detectadas  | Peso relativo |
|-----------|--------------------------|---------------|
| RSI       | Regular + oculta         | Base          |
| MFI       | Regular + oculta         | Mayor (volumen)|
| TSI       | Regular + oculta         | Mayor (suavizado)|

Una señal es más fuerte cuando múltiples indicadores confirman la misma divergencia.

---

## 3.4 Estructura de Mercado — BOS (Break of Structure)

BOS complementa las divergencias proporcionando contexto de tendencia. Un Break of Structure confirma que la tendencia ha cambiado.

### Definición

```
Tendencia alcista:
  - Higher Highs (HH) y Higher Lows (HL)
  - BOS bajista: precio rompe por debajo del último HL

Tendencia bajista:
  - Lower Lows (LL) y Lower Highs (LH)
  - BOS alcista: precio rompe por encima del último LH
```

### Algoritmo BOS

```python
def detect_bos(pivots: pd.DataFrame) -> pd.Series:
    """
    Recorre los pivot highs y lows secuencialmente.
    Mantiene registro del último swing high y swing low.

    BOS alcista:
      - El precio cierra por encima del último Lower High
        en contexto de tendencia bajista

    BOS bajista:
      - El precio cierra por debajo del último Higher Low
        en contexto de tendencia alcista

    Returns:
      Series con valores: 'bullish_bos', 'bearish_bos', o NaN
    """
```

### Contexto de tendencia

El BOS se combina con EMAs para definir el régimen actual:

| Contexto           | Condición                                | Operaciones permitidas |
|--------------------|------------------------------------------|------------------------|
| Tendencia alcista  | BOS alcista + precio > EMA50             | Solo longs             |
| Tendencia bajista  | BOS bajista + precio < EMA50             | Solo shorts            |
| Transición         | BOS reciente pero EMAs no alineadas      | Reducir tamaño         |
| Sin tendencia      | Sin BOS claro, EMAs entrelazadas         | No operar              |

---

## 3.5 Salida de Capa 3 — Signal Object

Cada señal generada es un objeto estructurado:

```python
@dataclass
class Signal:
    timestamp: int
    pair: str
    direction: str            # 'long' | 'short'
    signal_type: str          # 'regular_bullish' | 'hidden_bullish' | etc.
    divergence_indicators: list  # ['rsi', 'mfi'] — cuáles detectaron
    bos_confirmed: bool       # ¿hay BOS en la misma dirección?
    trend_context: str        # 'bullish' | 'bearish' | 'neutral'
    ema_alignment: str        # 'aligned' | 'partial' | 'contra'
    price_at_signal: float
    atr_at_signal: float
    rsi_value: float
    mfi_value: float
    tsi_value: float
```

Esta señal cruda pasa a **Capa 4 (Confluence Score)** para ser evaluada y puntuada.

---

## 3.6 Filtros Pre-Confluence

Antes de enviar a Capa 4, se aplican filtros duros que descartan señales inválidas:

| Filtro                          | Regla                                     | Razón                              |
|---------------------------------|-------------------------------------------|------------------------------------|
| Warmup incompleto               | Descartar si < 600 velas disponibles     | Indicadores no estables            |
| Sin divergencia                 | Mínimo 1 indicador con divergencia       | No hay señal base                  |
| Contra-tendencia sin BOS        | No tomar long si tendencia bajista sin BOS| Evitar contratendencia ciega       |
| ATR extremadamente bajo         | Descartar si ATR < percentil 10 histórico| Mercado sin movimiento             |
| Señal duplicada                 | No repetir misma señal en < 3 velas      | Evitar spam                        |
