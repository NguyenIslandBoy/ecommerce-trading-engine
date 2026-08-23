"""Tests for Layers 2 and 3.

Weighted toward the things that would be quietly catastrophic rather than the
things that would be obvious. A detector that fails to fire gets noticed; a
detector that reads a censored cohort as a collapse, or an autonomy gate that
auto-executes an action its own simulation rejects, produces a confident wrong
answer nobody questions.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from engine.recommend import (
    AUTO_CAPPED,
    AUTO_EXECUTE,
    FLAG_FOR_REVIEW,
    MONITOR,
    WAIT,
    decide_autonomy,
    dedupe,
    Recommendation,
)
from engine.signals import Classification, Direction, Signal, Tier
from engine.simulate import Outcome, estimate_beta, simulate_reorder
from engine.stats import (
    benjamini_hochberg,
    deseasonalise_dow,
    mad_zscore,
    mann_kendall,
    persistent,
    theil_sen_slope,
)


# --------------------------------------------------------------------------
# Robust statistics
# --------------------------------------------------------------------------

def test_theil_sen_recovers_a_known_slope():
    assert theil_sen_slope([0, 2, 4, 6, 8, 10]) == pytest.approx(2.0)


def test_theil_sen_survives_an_outlier_that_breaks_least_squares():
    """The whole reason for using it: a December spike must not set the trend."""
    clean = list(range(20))
    spiked = clean.copy()
    spiked[10] = 500                      # one enormous day

    theil = theil_sen_slope(spiked)
    least_squares = float(np.polyfit(np.arange(len(spiked)), spiked, 1)[0])

    # Theil-Sen recovers the true slope exactly; least squares is dragged by
    # roughly a third even with the spike at the point of LEAST leverage.
    assert theil == pytest.approx(1.0, abs=0.01)
    assert abs(least_squares - 1.0) > 10 * abs(theil - 1.0)
    assert least_squares > 1.3


def test_mann_kendall_separates_trend_from_noise():
    z_trend, p_trend = mann_kendall(list(range(15)))
    z_flat, p_flat = mann_kendall([5] * 15)

    assert p_trend < 0.001 and z_trend > 0
    assert p_flat == pytest.approx(1.0)


def test_mann_kendall_needs_enough_points():
    z, p = mann_kendall([1, 2, 3])
    assert np.isnan(z) and np.isnan(p)


def test_mad_zscore_flags_an_outlier_and_ignores_a_constant_series():
    assert mad_zscore([10, 10, 11, 10, 9, 10, 10], 30) > 10
    assert mad_zscore([7, 7, 7, 7, 7], 7) == 0.0


def test_benjamini_hochberg_keeps_small_p_and_drops_large():
    keep = benjamini_hochberg([0.001, 0.008, 0.04, 0.6, 0.9], alpha=0.10)
    assert list(keep) == [True, True, True, False, False]


def test_benjamini_hochberg_is_less_brutal_than_bonferroni():
    """The reason FDR was chosen: at this scale Bonferroni rejects real signals."""
    p_values = [0.001, 0.004, 0.006, 0.02, 0.5, 0.7, 0.8, 0.9, 0.95, 0.99]
    kept = benjamini_hochberg(p_values, alpha=0.10).sum()
    bonferroni = sum(p <= 0.10 / len(p_values) for p in p_values)
    assert kept > bonferroni


def test_deseasonalise_removes_a_day_of_week_pattern():
    days = list(range(1, 8)) * 8
    # Saturday (6) sells at double rate; everything else flat.
    values = [20.0 if d == 6 else 10.0 for d in days]
    frame = pd.DataFrame({"iso_dow": days, "value": values})

    adjusted, factors = deseasonalise_dow(frame, "value")

    assert factors[6] == pytest.approx(2.0)
    assert adjusted.std() == pytest.approx(0.0, abs=1e-9)


def test_persistence_requires_consecutive_flags():
    assert persistent([0, 1, 1, 1], 3) is True
    assert persistent([1, 1, 0, 1], 3) is False
    assert persistent([1, 1], 3) is False


# --------------------------------------------------------------------------
# The signal contract
# --------------------------------------------------------------------------

def _signal(**overrides) -> Signal:
    base = dict(
        detector="cac_trend", entity_type="channel", entity_id="meta",
        as_of_date=dt.date(2025, 6, 30), fired_date=dt.date(2025, 6, 30),
        severity=1.0, direction=Direction.DEGRADING,
    )
    base.update(overrides)
    return Signal(**base)


def test_tier_c_confidence_cannot_reach_the_autonomy_threshold():
    """Structural, not a threshold choice: 26.8% of orders are unattributed."""
    perfect_tier_c = _signal(severity=1.0, attribution_tier=Tier.C)
    assert perfect_tier_c.confidence <= 0.55


def test_confidence_is_discounted_by_a_dirty_window():
    clean = _signal(severity=1.0, attribution_tier=Tier.A, data_quality_score=1.0)
    dirty = _signal(severity=1.0, attribution_tier=Tier.A, data_quality_score=0.3)
    assert dirty.confidence < clean.confidence


@pytest.mark.parametrize("classification,actionable", [
    (Classification.COMMERCIAL, True),
    (Classification.ARTIFACT, False),
    (Classification.DATA_QUALITY, False),
])
def test_only_commercial_signals_are_actionable(classification, actionable):
    assert _signal(classification=classification).is_actionable is actionable


def test_a_signal_that_failed_fdr_is_not_actionable():
    assert _signal(passed_fdr=False).is_actionable is False


def test_signal_id_is_stable_across_runs():
    assert _signal().signal_id == _signal().signal_id


# --------------------------------------------------------------------------
# The autonomy gate -- Layer 3's safety property
# --------------------------------------------------------------------------

CFG = {"autonomy": {
    "high_confidence": 0.80, "medium_confidence": 0.50,
    "medium_band_magnitude_cap": 0.10, "min_p_positive": 0.55,
    "tier_c_may_auto_execute": False,
}}


def _outcome(p_positive, median=100.0):
    draws = np.array([median])
    return Outcome("m", draws, p_positive, -10.0, 210.0, median, {})


def test_irreversible_actions_are_never_auto_executed():
    """No confidence buys autonomy over capital that cannot be unspent."""
    decision, _ = decide_autonomy(0.99, "irreversible", Tier.A, CFG,
                                  outcome=_outcome(0.95))
    assert decision == FLAG_FOR_REVIEW


def test_tier_c_never_auto_executes_even_when_reversible():
    decision, notes = decide_autonomy(0.95, "reversible", Tier.C, CFG,
                                      outcome=_outcome(0.95))
    assert decision == FLAG_FOR_REVIEW
    assert any("Tier C" in n for n in notes)


def test_high_confidence_reversible_tier_a_auto_executes():
    decision, _ = decide_autonomy(0.9, "reversible", Tier.A, CFG,
                                  outcome=_outcome(0.9))
    assert decision == AUTO_EXECUTE


def test_medium_confidence_is_capped_not_refused():
    decision, notes = decide_autonomy(0.6, "reversible", Tier.A, CFG,
                                      outcome=_outcome(0.9))
    assert decision == AUTO_CAPPED
    assert any("capped" in n for n in notes)


def test_low_confidence_only_monitors():
    decision, _ = decide_autonomy(0.2, "reversible", Tier.A, CFG,
                                  outcome=_outcome(0.9))
    assert decision == MONITOR


def test_an_action_its_own_simulation_rejects_is_not_taken():
    """The bug this was written for: a refresh auto-executed at median -817."""
    decision, notes = decide_autonomy(0.95, "reversible", Tier.A, CFG,
                                      outcome=_outcome(0.13, median=-817.0))
    assert decision == MONITOR
    assert any("does not pay" in n for n in notes)


def test_the_simulation_gate_outranks_high_confidence():
    """Confidence says the SIGNAL is real; p_positive says ACTING pays."""
    rejected, _ = decide_autonomy(0.99, "reversible", Tier.A, CFG,
                                  outcome=_outcome(0.10))
    accepted, _ = decide_autonomy(0.99, "reversible", Tier.A, CFG,
                                  outcome=_outcome(0.90))
    assert rejected == MONITOR
    assert accepted == AUTO_EXECUTE


def test_investigations_are_raised_never_executed():
    decision, _ = decide_autonomy(0.99, "reversible", Tier.A, CFG,
                                  action_type="INVESTIGATE_DATA")
    assert decision == FLAG_FOR_REVIEW


def test_a_coin_flip_becomes_wait_when_more_data_would_settle_it():
    voi = {"recommend_wait": True, "reason": "another week would settle it"}
    decision, _ = decide_autonomy(0.9, "reversible", Tier.A, CFG, voi=voi,
                                  outcome=_outcome(0.9))
    assert decision == WAIT


def test_dedupe_keeps_the_best_evidenced_and_records_the_other():
    def rec(detector, confidence):
        return Recommendation(
            action_type="REALLOCATE_SPEND", entity_id="meta -> google",
            magnitude=0.2, magnitude_unit="share", rationale="",
            confidence=confidence, reversibility="reversible",
            autonomy=AUTO_EXECUTE, source_signal_id="x",
            source_detector=detector,
        )

    merged = dedupe([rec("cac_trend", 0.87), rec("cpc_decomposition", 0.50)])

    assert len(merged) == 1
    assert merged[0].source_detector == "cac_trend"
    assert any("cpc_decomposition" in n for n in merged[0].notes)


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------

def test_beta_is_never_zero_so_reallocation_is_never_free():
    """beta = 0 makes every budget shift look profitable. It is the classic way
    this kind of model produces nonsense, so it is floored, not fitted to it."""
    empty = pd.DataFrame(columns=["channel", "ad_spend", "new_customers", "date_day"])
    beta, se = estimate_beta(empty, "google")
    assert beta > 0 and se > 0


def test_beta_is_floored_when_the_data_says_customers_get_cheaper():
    """Over 90 days that is budget tracking demand, not increasing returns."""
    days = pd.date_range("2025-01-01", periods=60, freq="D")
    # Spend up, CAC down -- a negative elasticity.
    frame = pd.DataFrame({
        "date_day": days,
        "channel": "google",
        "ad_spend": np.linspace(100, 1000, 60),
        "new_customers": np.linspace(5, 200, 60),
    })
    beta, _ = estimate_beta(frame, "google")
    assert beta > 0


def test_reorder_outcome_has_no_p_positive():
    """Margin-at-risk is negative by construction; P(>0)=0% would read as
    'certain to lose money' when it means 'this is a loss measure'."""
    days = pd.date_range("2025-01-01", periods=90, freq="D")
    product = pd.DataFrame({
        "date_day": days, "sku": "TEST-1", "product_title": "Test",
        "units": np.full(90, 5.0), "inventory_quantity": np.full(90, 40.0),
        "net_revenue": np.full(90, 100.0), "contribution_margin": np.full(90, 70.0),
    })
    outcome = simulate_reorder(product, "TEST-1", lead_time_days=21, unit_margin=14.0)

    assert outcome.p_positive is None
    assert outcome.assumptions["p_stockout"] > 0.9   # 40 units, 5/day, 21 days
    assert outcome.summary()["p_positive"] is None


def test_simulation_returns_a_distribution_not_a_point():
    days = pd.date_range("2025-01-01", periods=90, freq="D")
    product = pd.DataFrame({
        "date_day": days, "sku": "TEST-1", "product_title": "Test",
        "units": np.random.default_rng(0).poisson(5, 90).astype(float),
        "inventory_quantity": np.full(90, 200.0),
        "net_revenue": np.full(90, 100.0), "contribution_margin": np.full(90, 70.0),
    })
    outcome = simulate_reorder(product, "TEST-1", lead_time_days=21, unit_margin=14.0)

    assert outcome.draws.size == 10_000
    assert outcome.ci_low <= outcome.median <= outcome.ci_high
