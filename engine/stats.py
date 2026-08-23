"""Robust statistics for detection.

Everything here is chosen for one reason: this series has a November/December
peak and a January trough, and 12 months of history. Mean-and-standard-deviation
methods fire on the peak; least-squares slopes are dragged by it. So the trend
test is Theil-Sen, the significance test is Mann-Kendall, and outliers are scored
against the median absolute deviation.

The seasonal treatment is deliberately limited. The spec asked for a
day-of-week x month-of-year baseline, but the extract covers 2024-07 to 2025-06:
exactly one observation per calendar month. Month-of-year effects are perfectly
confounded with trend and cannot be identified, so only the day-of-week
component is removed and the limitation is reported rather than papered over.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as _sps

MIN_TREND_POINTS = 8


def theil_sen_slope(values) -> float:
    """Median of all pairwise slopes. Per-period change, in the series' units.

    Breakdown point of ~29%: the December spike has to be more than a quarter of
    the window before it can drag the estimate, which least squares cannot say.
    """
    y = np.asarray(pd.Series(values).dropna(), dtype=float)
    if y.size < 2:
        return float("nan")
    x = np.arange(y.size, dtype=float)
    idx_i, idx_j = np.triu_indices(y.size, k=1)
    dx = x[idx_j] - x[idx_i]
    slopes = (y[idx_j] - y[idx_i]) / dx
    return float(np.median(slopes))


def mann_kendall(values) -> tuple[float, float]:
    """Monotonic-trend test. Returns (z, two-sided p).

    Non-parametric and rank-based, so it does not assume the residuals are
    normal -- which for daily CAC they are not.
    """
    y = np.asarray(pd.Series(values).dropna(), dtype=float)
    n = y.size
    if n < MIN_TREND_POINTS:
        return float("nan"), float("nan")

    s = float(np.sum(np.sign(y[None, :] - y[:, None])[np.triu_indices(n, k=1)]))

    # Tie correction: flat stretches (a channel spending the same each day)
    # otherwise inflate the variance and make a real trend look insignificant.
    _, counts = np.unique(y, return_counts=True)
    tie_term = float(np.sum(counts * (counts - 1) * (2 * counts + 5)))
    variance = (n * (n - 1) * (2 * n + 5) - tie_term) / 18.0
    if variance <= 0:
        return 0.0, 1.0

    if s > 0:
        z = (s - 1) / np.sqrt(variance)
    elif s < 0:
        z = (s + 1) / np.sqrt(variance)
    else:
        z = 0.0
    return float(z), float(2 * (1 - _sps.norm.cdf(abs(z))))


def mad_zscore(values, point=None) -> float:
    """Robust z-score of ``point`` against ``values``.

    0.6745 rescales MAD to be comparable with a standard deviation under
    normality, so the usual 3-sigma intuition still reads correctly.
    """
    y = np.asarray(pd.Series(values).dropna(), dtype=float)
    if y.size < 3:
        return float("nan")
    if point is None:
        point, y = y[-1], y[:-1]
    median = np.median(y)
    mad = np.median(np.abs(y - median))
    if mad == 0:
        # Degenerate window: fall back to a spread that at least exists, and
        # report no anomaly if the series is genuinely constant.
        spread = np.std(y)
        if spread == 0:
            return 0.0
        return float((point - median) / spread)
    return float(0.6745 * (point - median) / mad)


def benjamini_hochberg(p_values, alpha: float = 0.10) -> np.ndarray:
    """Step-up FDR control. Returns a boolean mask of the discoveries kept.

    11 detectors x ~20 entities x 365 cursors manufactures false positives by
    construction. Controlling the family-wise error rate (Bonferroni) at that
    scale would reject everything; controlling the false DISCOVERY rate keeps
    power while bounding the share of fired signals that are noise.
    """
    p = np.asarray(p_values, dtype=float)
    keep = np.zeros(p.size, dtype=bool)
    testable = ~np.isnan(p)
    if not testable.any():
        return keep

    idx = np.flatnonzero(testable)
    order = idx[np.argsort(p[idx])]
    m = order.size
    thresholds = alpha * np.arange(1, m + 1) / m
    passing = p[order] <= thresholds
    if passing.any():
        cutoff = np.flatnonzero(passing)[-1]
        keep[order[: cutoff + 1]] = True
    return keep


def deseasonalise_dow(frame: pd.DataFrame, value_col: str, dow_col: str = "iso_dow"):
    """Remove the day-of-week component, multiplicatively.

    Each weekday has ~52 observations, so this factor is estimable. Month-of-year
    is not -- see the module docstring. Returns (adjusted series, factors).
    """
    working = frame[[dow_col, value_col]].dropna()
    if working.empty:
        return frame[value_col], {}

    overall = working[value_col].median()
    if not overall or np.isnan(overall):
        return frame[value_col], {}

    factors = (working.groupby(dow_col)[value_col].median() / overall).to_dict()
    factors = {k: (v if v and v > 0 else 1.0) for k, v in factors.items()}
    divisor = frame[dow_col].map(factors).fillna(1.0)
    return frame[value_col] / divisor, factors


def persistent(flags, required: int) -> bool:
    """True when the last ``required`` periods are all flagged.

    The blunt instrument that removes single-day noise, and the reason a
    one-off spike in a 365-day replay does not become a recommendation.
    """
    series = list(flags)
    if len(series) < required or required <= 0:
        return False
    return all(bool(x) for x in series[-required:])


def severity_from_z(z: float, cap: float = 6.0) -> float:
    """Map a z-score onto 0-1 so detectors of different kinds stay comparable."""
    if z is None or np.isnan(z):
        return 0.0
    return float(min(abs(z), cap) / cap)
