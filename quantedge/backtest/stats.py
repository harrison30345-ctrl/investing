"""
Uncertainty statistics for backtest results.

Point estimates (one Sharpe ratio, one net return) hide how much luck is in a
backtest. Every experiment therefore also records:

  * Block-bootstrap 95% confidence intervals for the Sharpe ratio and for the
    annualised excess return over each benchmark. Daily returns are resampled in
    20-day blocks (shorter for short samples, so each resample holds at least ~10
    blocks), so volatility clustering and autocorrelation survive the resampling.
    Seeded, so the same data always gives the same interval.
  * A t-statistic and bootstrap interval for the mean trade return.
  * The Probabilistic Sharpe Ratio (PSR): the probability that the true Sharpe
    is above zero, given the sample length, skew and fat tails (Bailey & López
    de Prado, 2012).
  * The Deflated Sharpe Ratio (DSR): the PSR measured against the best Sharpe
    you'd expect from N tries of a strategy with no edge. N is the number of
    experiments run on the same strategy and universe. Trying more variants
    raises the bar (Bailey & López de Prado, 2014).

Simplification: the DSR uses the sampling variance of a single Sharpe
estimate as the variance across trials, which is the standard "independent
trials under the null" approximation. Correlated variants (e.g. neighbouring
parameter values) make it conservative.
"""
from __future__ import annotations

import math
from statistics import NormalDist
from typing import Callable, Dict, Optional, Tuple

import numpy as np

PERIODS = 252
N_BOOT = 2000
BLOCK = 20
SEED = 20261026
_N = NormalDist()
EULER_GAMMA = 0.5772156649015329


def _block_indices(n: int, n_boot: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """(n_boot, n) index matrix built from random contiguous blocks (moving-block bootstrap)."""
    n_blocks = int(math.ceil(n / block))
    starts = rng.integers(0, max(n - block + 1, 1), size=(n_boot, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1)[:, :n]
    return np.minimum(idx, n - 1)


def bootstrap_ci(x: np.ndarray, stat: Callable[[np.ndarray], np.ndarray], *, block: int = BLOCK,
                 n_boot: int = N_BOOT, seed: int = SEED, level: float = 0.95) -> Tuple[float, float]:
    """`stat` maps a (n_boot, n) array to n_boot statistics (vectorised)."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 10:
        return float("nan"), float("nan")
    # Short samples (e.g. ~24 intraday sessions) can't hold 20-day blocks: with only one or two blocks
    # the resamples barely differ and the interval collapses. Keep at least ~10 blocks per resample.
    block = max(1, min(block, len(x) // 10))
    rng = np.random.default_rng(seed)
    samples = x[_block_indices(len(x), n_boot, block, rng)]
    vals = stat(samples)
    vals = vals[np.isfinite(vals)]
    if len(vals) < n_boot // 2:          # e.g. a flat equity curve: Sharpe undefined in most resamples
        return float("nan"), float("nan")
    a = (1 - level) / 2 * 100
    return float(np.percentile(vals, a)), float(np.percentile(vals, 100 - a))


def _sharpe_rows(s: np.ndarray) -> np.ndarray:
    sd = s.std(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sd > 0, s.mean(axis=1) / sd * math.sqrt(PERIODS), np.nan)


def _ann_mean_rows(s: np.ndarray) -> np.ndarray:
    return s.mean(axis=1) * PERIODS


def sharpe_ci(daily: np.ndarray) -> Tuple[float, float]:
    return bootstrap_ci(daily, _sharpe_rows)


def excess_ci(daily: np.ndarray, bench_daily: np.ndarray) -> Tuple[float, float]:
    """CI for the annualised mean daily return difference (strategy − benchmark)."""
    return bootstrap_ci(np.asarray(daily) - np.asarray(bench_daily), _ann_mean_rows)


def sharpe_diff_ci(daily: np.ndarray, bench_daily: np.ndarray, *, block: int = BLOCK, n_boot: int = N_BOOT,
                   seed: int = SEED, level: float = 0.95) -> Tuple[float, float, float]:
    """
    (point, low, high) for Sharpe(strategy) − Sharpe(benchmark), annualised. Paired moving-block
    bootstrap: each resample takes the same days for both series, so their correlation is kept.
    """
    a, b = np.asarray(daily, dtype=float), np.asarray(bench_daily, dtype=float)
    ok = np.isfinite(a) & np.isfinite(b)
    a, b = a[ok], b[ok]
    if len(a) < 10:
        return float("nan"), float("nan"), float("nan")
    point = float(_sharpe_rows(a[None, :])[0] - _sharpe_rows(b[None, :])[0])
    block = max(1, min(block, len(a) // 10))
    idx = _block_indices(len(a), n_boot, block, np.random.default_rng(seed))
    vals = _sharpe_rows(a[idx]) - _sharpe_rows(b[idx])
    vals = vals[np.isfinite(vals)]
    if len(vals) < n_boot // 2:
        return point, float("nan"), float("nan")
    q = (1 - level) / 2 * 100
    return point, float(np.percentile(vals, q)), float(np.percentile(vals, 100 - q))


def trade_stats(trade_returns: np.ndarray) -> Dict[str, float]:
    r = np.asarray(trade_returns, dtype=float)
    r = r[np.isfinite(r)]
    out: Dict[str, float] = {}
    if len(r) >= 2 and r.std(ddof=1) > 0:
        out["trade_tstat"] = float(r.mean() / (r.std(ddof=1) / math.sqrt(len(r))))
    if len(r) >= 10:
        lo, hi = bootstrap_ci(r, lambda s: s.mean(axis=1), block=1)
        out["trade_mean_ci_low"], out["trade_mean_ci_high"] = lo, hi
    return out


def _moments(daily: np.ndarray) -> Tuple[float, float, float, int]:
    r = np.asarray(daily, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    sd = r.std(ddof=1) if n > 1 else 0.0
    if n < 3 or sd == 0:
        return float("nan"), 0.0, 3.0, n
    sr = r.mean() / sd                                   # per-period Sharpe
    z = (r - r.mean()) / r.std(ddof=0)
    return float(sr), float((z ** 3).mean()), float((z ** 4).mean()), n


def _sr_std(sr: float, skew: float, kurt: float, n: int) -> float:
    """Standard error of a per-period Sharpe estimate under non-normal returns."""
    return math.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr ** 2, 1e-12) / (n - 1))


def probabilistic_sharpe(daily: np.ndarray, benchmark_sr: float = 0.0) -> float:
    """P(true per-period Sharpe > benchmark_sr)."""
    sr, skew, kurt, n = _moments(daily)
    if not np.isfinite(sr) or n < 3:
        return float("nan")
    return float(_N.cdf((sr - benchmark_sr) / _sr_std(sr, skew, kurt, n)))


def expected_max_sharpe(n_trials: int, sr_std: float) -> float:
    """Expected best per-period Sharpe among n_trials no-edge strategies."""
    if n_trials <= 1:
        return 0.0
    return sr_std * ((1 - EULER_GAMMA) * _N.inv_cdf(1 - 1 / n_trials)
                     + EULER_GAMMA * _N.inv_cdf(1 - 1 / (n_trials * math.e)))


def deflated_sharpe(daily: np.ndarray, n_trials: int) -> float:
    sr, skew, kurt, n = _moments(daily)
    if not np.isfinite(sr) or n < 3:
        return float("nan")
    sd = _sr_std(0.0, skew, kurt, n)       # spread of Sharpe estimates when the true Sharpe is zero
    return probabilistic_sharpe(daily, expected_max_sharpe(max(n_trials, 1), sd))


def uncertainty(daily: np.ndarray, bench_daily: Optional[np.ndarray], universe_daily: Optional[np.ndarray],
                trade_returns: np.ndarray) -> Dict[str, float]:
    """All interval statistics for one result (no trial count; see deflated_sharpe)."""
    out: Dict[str, float] = {}
    out["sharpe_ci_low"], out["sharpe_ci_high"] = sharpe_ci(daily)
    if bench_daily is not None:
        out["excess_ann_ci_low"], out["excess_ann_ci_high"] = excess_ci(daily, bench_daily)
        (out["sharpe_diff_vs_benchmark"], out["sharpe_diff_ci_low"],
         out["sharpe_diff_ci_high"]) = sharpe_diff_ci(daily, bench_daily)
    if universe_daily is not None:
        out["excess_universe_ann_ci_low"], out["excess_universe_ann_ci_high"] = excess_ci(daily, universe_daily)
    out["psr"] = probabilistic_sharpe(daily)
    out.update(trade_stats(trade_returns))
    return {k: v for k, v in out.items() if v is not None and np.isfinite(v)}
