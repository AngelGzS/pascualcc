"""Walk-forward optimizer with Optuna for Bayesian optimization."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config import settings
from indicators.calculator import calculate_all_indicators
from backtest.engine import BacktestEngine
from backtest.metrics import BacktestMetrics, calculate_metrics, format_report

logger = logging.getLogger(__name__)


@dataclass
class WindowResult:
    """Results for a single walk-forward window."""
    window_id: int
    is_params: dict = field(default_factory=dict)
    is_metrics: BacktestMetrics = field(default_factory=BacktestMetrics)
    oos_metrics: BacktestMetrics = field(default_factory=BacktestMetrics)
    wfe: float = 0.0


@dataclass
class WalkForwardResult:
    """Aggregate results from walk-forward analysis."""
    windows: list[WindowResult] = field(default_factory=list)
    aggregated_oos_metrics: BacktestMetrics = field(default_factory=BacktestMetrics)
    best_params: dict = field(default_factory=dict)
    verdict: str = "NO_GO"
    verdict_details: list[str] = field(default_factory=list)
    avg_wfe: float = 0.0


class WalkForwardOptimizer:
    """Walk-forward testing with Bayesian parameter optimization."""

    def __init__(
        self,
        in_sample_days: int = settings.WF_IN_SAMPLE_DAYS,
        out_sample_days: int = settings.WF_OUT_SAMPLE_DAYS,
        n_trials: int = settings.OPTUNA_N_TRIALS,
    ) -> None:
        self.in_sample_days = in_sample_days
        self.out_sample_days = out_sample_days
        self.n_trials = n_trials

    def run(self, df: pd.DataFrame, pair: str) -> WalkForwardResult:
        """Run walk-forward optimization on a DataFrame.

        The DataFrame should have OHLCV columns (indicators will be computed here).
        """
        # Calculate indicators
        df = calculate_all_indicators(df)

        # Determine candles per day based on timeframe
        if len(df) >= 2:
            interval_ms = int(df["timestamp"].iloc[1] - df["timestamp"].iloc[0])
            candles_per_day = 86_400_000 // interval_ms
        else:
            candles_per_day = 96  # 15m default

        is_candles = self.in_sample_days * candles_per_day
        oos_candles = self.out_sample_days * candles_per_day
        window_size = is_candles + oos_candles

        total_candles = len(df)
        if total_candles < window_size:
            logger.error(
                "Not enough data: %d candles, need at least %d for one window",
                total_candles, window_size,
            )
            return WalkForwardResult(verdict="NO_GO", verdict_details=["Insufficient data"])

        # Calculate windows
        windows: list[tuple[int, int, int, int]] = []  # (is_start, is_end, oos_start, oos_end)
        start = 0
        while start + window_size <= total_candles:
            is_start = start
            is_end = start + is_candles
            oos_start = is_end
            oos_end = min(is_end + oos_candles, total_candles)
            windows.append((is_start, is_end, oos_start, oos_end))
            start += oos_candles  # Slide by OOS period (no overlap)

        if not windows:
            return WalkForwardResult(verdict="NO_GO", verdict_details=["No valid windows"])

        logger.info("Walk-forward: %d windows, IS=%d candles, OOS=%d candles", len(windows), is_candles, oos_candles)

        # Process each window
        result = WalkForwardResult()
        all_oos_trades = []

        for w_idx, (is_start, is_end, oos_start, oos_end) in enumerate(windows):
            logger.info("=== Window %d/%d ===", w_idx + 1, len(windows))

            is_df = df.iloc[is_start:is_end].reset_index(drop=True)
            oos_df = df.iloc[oos_start:oos_end].reset_index(drop=True)

            # Optimize on in-sample
            best_params = self._optimize(is_df, pair)

            # Run in-sample with best params
            is_engine = self._create_engine(best_params)
            is_trades = is_engine.run(is_df, pair)
            is_metrics = calculate_metrics(is_trades, settings.INITIAL_CAPITAL, self.in_sample_days)

            # Run out-of-sample with best params
            oos_engine = self._create_engine(best_params)
            oos_trades = oos_engine.run(oos_df, pair)
            oos_metrics = calculate_metrics(oos_trades, settings.INITIAL_CAPITAL, self.out_sample_days)

            # WFE
            if is_metrics.total_pnl_percent != 0:
                wfe = oos_metrics.total_pnl_percent / is_metrics.total_pnl_percent
            else:
                wfe = 0.0

            window_result = WindowResult(
                window_id=w_idx,
                is_params=best_params,
                is_metrics=is_metrics,
                oos_metrics=oos_metrics,
                wfe=wfe,
            )
            result.windows.append(window_result)
            all_oos_trades.extend(oos_trades)

            logger.info(
                "Window %d: IS PnL=%.1f%%, OOS PnL=%.1f%%, WFE=%.0f%%",
                w_idx + 1,
                is_metrics.total_pnl_percent * 100,
                oos_metrics.total_pnl_percent * 100,
                wfe * 100,
            )

        # Aggregate OOS results
        total_oos_days = self.out_sample_days * len(windows)
        result.aggregated_oos_metrics = calculate_metrics(all_oos_trades, settings.INITIAL_CAPITAL, total_oos_days)
        result.avg_wfe = float(np.mean([w.wfe for w in result.windows])) if result.windows else 0.0

        # Use best params from last window
        if result.windows:
            result.best_params = result.windows[-1].is_params

        # Validate
        result.verdict, result.verdict_details = self._validate(result)

        return result

    def _optimize(self, df: pd.DataFrame, pair: str) -> dict:
        """Optimize parameters on in-sample data using Optuna."""
        try:
            import optuna
            optuna.logging.set_verbosity(optuna.logging.WARNING)
        except ImportError:
            logger.warning("Optuna not available, using default parameters")
            return self._default_params()

        best_score = float("-inf")
        best_params = self._default_params()

        def objective(trial: optuna.Trial) -> float:
            nonlocal best_score, best_params

            params = {
                "atr_multiplier": trial.suggest_float("atr_multiplier", 1.0, 3.0, step=0.25),
                "confluence_threshold": trial.suggest_int("confluence_threshold", 40, 75, step=5),
                "entry_factor": trial.suggest_float("entry_factor", 0.2, 1.0, step=0.2),
                "entry_timeout": trial.suggest_int("entry_timeout", 4, 12, step=2),
                "tp_multiplier": trial.suggest_float("tp_multiplier", 2.0, 5.0, step=0.5),
                "pivot_left": trial.suggest_int("pivot_left", 3, 7),
                "pivot_right": trial.suggest_int("pivot_right", 3, 7),
            }

            engine = self._create_engine(params)
            trades = engine.run(df, pair)
            metrics = calculate_metrics(trades, settings.INITIAL_CAPITAL, self.in_sample_days)

            # Primary objective: Calmar ratio
            if metrics.total_trades < settings.WF_MIN_TRADES:
                return -100.0  # Penalty for too few trades

            score = metrics.calmar_ratio

            # Penalize extreme drawdown
            if metrics.max_drawdown > settings.WF_MAX_DRAWDOWN:
                score *= 0.5

            if score > best_score:
                best_score = score
                best_params = params.copy()

            return score

        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=self.n_trials, show_progress_bar=False)

        logger.info("Optimization complete. Best Calmar: %.2f", best_score)
        return best_params

    def _create_engine(self, params: dict) -> BacktestEngine:
        """Create a BacktestEngine with given parameters."""
        return BacktestEngine(
            atr_multiplier=params.get("atr_multiplier", settings.ATR_MULTIPLIER),
            confluence_threshold=int(params.get("confluence_threshold", settings.CONFLUENCE_THRESHOLD)),
            entry_factor=params.get("entry_factor", settings.ENTRY_FACTOR),
            entry_timeout=int(params.get("entry_timeout", settings.ENTRY_TIMEOUT)),
            tp_multiplier=params.get("tp_multiplier", settings.TP_MULTIPLIER),
            pivot_left=int(params.get("pivot_left", settings.PIVOT_LEFT)),
            pivot_right=int(params.get("pivot_right", settings.PIVOT_RIGHT)),
        )

    @staticmethod
    def _default_params() -> dict:
        return {
            "atr_multiplier": settings.ATR_MULTIPLIER,
            "confluence_threshold": settings.CONFLUENCE_THRESHOLD,
            "entry_factor": settings.ENTRY_FACTOR,
            "entry_timeout": settings.ENTRY_TIMEOUT,
            "tp_multiplier": settings.TP_MULTIPLIER,
            "pivot_left": settings.PIVOT_LEFT,
            "pivot_right": settings.PIVOT_RIGHT,
        }

    def _validate(self, result: WalkForwardResult) -> tuple[str, list[str]]:
        """Apply Go/No-Go criteria from spec."""
        details: list[str] = []
        passed = True
        m = result.aggregated_oos_metrics

        # Check: at least 70% of OOS windows are profitable (skip empty windows)
        if result.windows:
            active_windows = [w for w in result.windows if w.oos_metrics.total_trades > 0]
            if active_windows:
                profitable_windows = sum(1 for w in active_windows if w.oos_metrics.total_pnl_percent > -0.003)
                pct_profitable = profitable_windows / len(active_windows)
                skipped = len(result.windows) - len(active_windows)
                skip_note = f" ({skipped} empty windows excluded)" if skipped else ""
                if pct_profitable < 0.7:
                    passed = False
                    details.append(f"FAIL: Only {pct_profitable:.0%} OOS windows profitable (need >=70%){skip_note}")
                else:
                    details.append(f"PASS: {pct_profitable:.0%} OOS windows profitable{skip_note}")

        # Check: Calmar ratio OOS > 50% of IS (cap IS Calmar to avoid infinity with few trades)
        if result.windows:
            is_calmars = [min(w.is_metrics.calmar_ratio, 8.0) for w in result.windows]
            avg_is_calmar = float(np.mean(is_calmars))
            threshold = avg_is_calmar * settings.CALMAR_RATIO_DECAY
            if avg_is_calmar > 0 and m.calmar_ratio < threshold:
                passed = False
                details.append(
                    f"FAIL: OOS Calmar {m.calmar_ratio:.2f} < {settings.CALMAR_RATIO_DECAY:.0%} of IS Calmar {avg_is_calmar:.2f} (capped at 8)"
                )
            else:
                details.append(f"PASS: OOS Calmar ratio {m.calmar_ratio:.2f}")

        # Check: profit factor > 1.0
        if m.profit_factor < 1.0:
            passed = False
            details.append(f"FAIL: Profit factor {m.profit_factor:.2f} < 1.0")
        else:
            details.append(f"PASS: Profit factor {m.profit_factor:.2f}")

        # Check: max drawdown <= 20%
        if m.max_drawdown > 0.20:
            passed = False
            details.append(f"FAIL: Max drawdown {m.max_drawdown:.1%} > 20%")
        else:
            details.append(f"PASS: Max drawdown {m.max_drawdown:.1%}")

        # Check: no window with > 10% loss
        for w in result.windows:
            if w.oos_metrics.total_pnl_percent < -0.10:
                passed = False
                details.append(f"FAIL: Window {w.window_id} lost {w.oos_metrics.total_pnl_percent:.1%} (>10%)")

        # Check: WFE
        if result.avg_wfe < settings.WFE_MIN:
            passed = False
            details.append(f"FAIL: WFE {result.avg_wfe:.0%} < {settings.WFE_MIN:.0%}")
        else:
            details.append(f"PASS: WFE {result.avg_wfe:.0%}")

        verdict = "GO" if passed else "NO_GO"
        return verdict, details


def format_walk_forward_report(result: WalkForwardResult, pair: str, timeframe: str) -> str:
    """Format a complete walk-forward report."""
    m = result.aggregated_oos_metrics
    lines = [
        "=== WALK-FORWARD REPORT ===",
        f"Par: {pair} | Timeframe: {timeframe}",
        f"Ventanas: {len(result.windows)}",
        "",
        "--- Resultados Out-of-Sample Agregados ---",
        f"Total trades:       {m.total_trades}",
        f"Win rate:           {m.win_rate:.1%}",
        f"Profit factor:      {m.profit_factor:.2f}",
        f"Avg R:R:            {m.avg_rr_ratio:.1f}:1",
        f"Max drawdown:       {m.max_drawdown:.1%}",
        f"Calmar ratio:       {m.calmar_ratio:.2f}",
        f"Sharpe ratio:       {m.sharpe_ratio:.2f}",
        f"WFE:                {result.avg_wfe:.0%}",
        "",
        "--- Por Ventana ---",
    ]

    for w in result.windows:
        om = w.oos_metrics
        lines.append(
            f"Ventana {w.window_id + 1} (OOS): "
            f"{om.total_pnl_percent:+.1%} | {om.total_trades} trades | "
            f"WR {om.win_rate:.0%} | MDD {om.max_drawdown:.1%}"
        )

    if result.best_params:
        lines.append("")
        lines.append("--- Parametros Optimos (ultima ventana) ---")
        for k, v in result.best_params.items():
            lines.append(f"{k}: {v}")

    lines.append("")
    verdict_emoji = "GO" if result.verdict == "GO" else "NO_GO"
    lines.append(f"--- VEREDICTO: {verdict_emoji} ---")
    for detail in result.verdict_details:
        lines.append(f"  {detail}")

    return "\n".join(lines)
