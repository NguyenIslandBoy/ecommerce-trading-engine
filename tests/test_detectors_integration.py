"""Integration tests against the real warehouse.

These are slower than the unit tests and worth it: they assert the behaviours
the whole project exists to get right, on the actual data, end to end.

Requires a built warehouse:
    cd dbt && ../venv/Scripts/dbt.exe build --profiles-dir .
    venv/Scripts/python.exe scripts/export_marts.py
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from engine.context import Warehouse
from engine.run import detect, signals_frame
from engine.signals import Classification

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def warehouse():
    try:
        return Warehouse()
    except FileNotFoundError as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="module")
def final_signals(warehouse):
    return signals_frame(detect(warehouse.at(warehouse.last_day)))


# --------------------------------------------------------------------------
# Censoring -- the failure this project is built to prevent, both directions
# --------------------------------------------------------------------------

def test_retention_collapse_fires_on_fully_exposed_cohorts(final_signals):
    rows = final_signals[final_signals["detector"] == "cohort_retention_shift"]
    assert len(rows) == 1

    signal = rows.iloc[0]
    assert signal["classification"] == Classification.COMMERCIAL.value

    evidence = signal["evidence"]
    assert evidence["first_rate_pct"] == pytest.approx(31.77, abs=0.05)
    assert evidence["last_rate_pct"] == pytest.approx(0.0, abs=0.01)
    # Every cohort in the comparison must have closed its window.
    assert evidence["fully_exposed_cohorts"] >= 9


def test_retention_stays_silent_before_any_cohort_is_exposed(warehouse):
    """The mirror test. At an early cursor the near-zero rates are unobserved,
    not measured, and reading them as a collapse is the exact error the
    censoring guards exist to prevent."""
    early = signals_frame(detect(warehouse.at(dt.date(2024, 11, 30))))
    rows = early[early["detector"] == "cohort_retention_shift"]
    assert rows.empty


def test_censored_cohorts_are_never_published_as_zero(warehouse):
    ctx = warehouse.at(warehouse.last_day)
    censored = ctx.retention[~ctx.retention["has_full_90d_exposure"].astype(bool)]
    assert not censored.empty                      # some must be censored
    assert censored["repeat_rate_90d"].isna().all()


# --------------------------------------------------------------------------
# Signal versus artifact
# --------------------------------------------------------------------------

def test_email_decline_is_classified_artifact_not_commercial(final_signals):
    """Opens fall ~20% across every flow while conversion holds. Acting on that
    would churn a programme that is still earning."""
    rows = final_signals[final_signals["detector"] == "email_engagement_decay"]
    assert len(rows) == 6                          # all six flows

    assert (rows["classification"] == Classification.ARTIFACT.value).all()
    assert not rows["is_actionable"].any()
    assert (rows["evidence"].map(lambda e: e["open_rate_change_pct"]) < -10).all()


def test_the_meta_outage_is_data_quality_not_a_cac_improvement(final_signals):
    rows = final_signals[final_signals["detector"] == "data_completeness"]
    assert len(rows) == 1

    signal = rows.iloc[0]
    assert signal["classification"] == Classification.DATA_QUALITY.value
    assert signal["entity_id"] == "meta_ads_daily"
    assert signal["evidence"]["missing_days"] == ["2025-03-15", "2025-03-16"]
    assert not signal["is_actionable"]


def test_blended_cac_is_null_on_the_outage_days(warehouse):
    """The concrete consequence: summed naively those days read GBP 6.75 and
    6.33 against a true figure near 11.50 -- a 40% understatement that looks
    like good news."""
    ctx = warehouse.at(warehouse.last_day)
    gap = ctx.daily[ctx.daily["date_day"].map(
        lambda d: str(d)[:10] in {"2025-03-15", "2025-03-16"}
    )]
    assert not gap.empty
    assert gap["blended_cac"].isna().all()
    assert not gap["ad_spend_is_complete"].any()


# --------------------------------------------------------------------------
# Point-in-time behaviour
# --------------------------------------------------------------------------

def test_a_cursor_cannot_see_the_future(warehouse):
    cursor = dt.date(2025, 3, 31)
    ctx = warehouse.at(cursor)
    assert pd.Timestamp(ctx.daily["date_day"].max()).date() <= cursor
    assert pd.Timestamp(ctx.product["date_day"].max()).date() <= cursor


def test_the_newest_day_of_a_historical_rebuild_has_no_spend(warehouse):
    """Ads land a day late, so at cursor D the spend for D has not arrived.
    Documented behaviour, not a gap -- a backtest must expect it 365 times."""
    cursor = dt.date(2025, 3, 31)
    ctx = warehouse.at(cursor)
    newest = ctx.daily[ctx.daily["date_day"].map(
        lambda d: pd.Timestamp(d).date() == cursor
    )]
    assert not newest.empty
    assert newest["ad_spend"].isna().all()
    assert newest["blended_cac"].isna().all()


def test_inventory_cover_refuses_to_run_on_a_historical_cursor(warehouse):
    """Stock is a current snapshot with no history. Firing it in the past
    invents stockouts that never happened -- it did so 80 times in January
    before this was enforced."""
    historical = signals_frame(detect(warehouse.at(dt.date(2025, 1, 31))))
    assert historical[historical["detector"] == "inventory_cover"].empty

    latest = signals_frame(detect(warehouse.at(warehouse.last_day)))
    assert not latest[latest["detector"] == "inventory_cover"].empty


def test_inventory_cover_finds_the_right_sku(final_signals):
    """The reorder is CBD Oil 20% 30ml at 17.0 days -- NOT the Vitamin D3
    breakout, which was stocked for its own growth. Conflating the velocity
    signal with the cover signal points the purchase order at the wrong SKU."""
    rows = final_signals[final_signals["detector"] == "inventory_cover"]
    skus = set(rows["entity_id"])
    assert "VIT-CBD20-30ML" in skus
    assert not any(sku.startswith("VIT-D3") for sku in skus)


def test_tiktok_never_produces_a_cac_signal(final_signals):
    """No cost file exists. Zero spend and no data are different statements."""
    assert final_signals[final_signals["entity_id"] == "tiktok"].empty


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------

@pytest.mark.parametrize("table", ["daily", "ads", "product", "email",
                                   "retention", "ltv"])
def test_signals_do_not_depend_on_row_order(warehouse, table):
    """Shuffling any input must change nothing.

    Theil-Sen and Mann-Kendall are sequence-dependent, so a trend computed over
    a scrambled series is meaningless. DuckDB scans in parallel and gives no
    ordering guarantee, and this held only by luck until a shuffle test turned
    14 signals into 12. The estimators now sort their own inputs.
    """
    baseline = signals_frame(detect(warehouse.at(warehouse.last_day)))

    shuffled = Warehouse()
    setattr(shuffled, table,
            getattr(shuffled, table).sample(frac=1.0, random_state=17)
            .reset_index(drop=True))
    after = signals_frame(detect(shuffled.at(shuffled.last_day)))

    assert set(after["signal_id"]) == set(baseline["signal_id"])


def test_the_briefs_worked_example_actually_fires(warehouse):
    """The brief's own example: "Meta CAC has exceeded the 60-day LTV threshold
    for 5 consecutive days".

    This detector scored zero across every cursor of the backtest while a
    qualifying run existed in the data. It counted the last five CALENDAR days,
    but ad spend lands a day late, so the window held only four days of cost at
    every cursor except one. A detector that can never fire fails silently, so
    it gets an explicit test.
    """
    signals = signals_frame(detect(warehouse.at(warehouse.latest_cursor)))
    breach = signals[signals["detector"] == "cac_ltv_breach"]

    assert not breach.empty, "the brief's worked example never fires"
    row = breach.iloc[0]
    assert row["entity_id"] == "meta"
    assert row["evidence"]["consecutive_days"] == 5
    assert row["evidence"]["ratio"] > 1.0        # CAC above LTV, by definition
    assert row["attribution_tier"] == "C"        # channel CAC is last-click


def test_a_channel_breach_cannot_act_on_its_own(warehouse):
    """Firing is not the same as acting.

    The brief's example implies a reallocation. This engine declines to make one
    unattended, because a channel-level CAC rests on last-click attribution and
    26.8% of non-cancelled orders have no usable referrer. It is a Tier C signal,
    so it monitors rather than executes — deliberately."""
    from engine.recommend import dedupe, recommend

    ctx = warehouse.at(warehouse.latest_cursor)
    recs = dedupe(recommend(ctx, signals_frame(detect(ctx))))
    breach = [r for r in recs if r.source_detector == "cac_ltv_breach"]

    assert breach, "no recommendation produced for the breach"
    assert all(r.autonomy in ("MONITOR", "FLAG FOR REVIEW") for r in breach)


def test_the_engine_stands_a_day_past_the_last_order(warehouse):
    """Ads land a day late, so standing on the final day of orders leaves the
    engine permanently one day short of its own cost data."""
    assert warehouse.latest_cursor == warehouse.last_day + dt.timedelta(days=1)

    ctx = warehouse.at(warehouse.latest_cursor)
    newest = ctx.daily[ctx.daily["date_day"].map(
        lambda d: pd.Timestamp(d).date() == warehouse.last_day)]
    assert not newest.empty
    assert newest["ad_spend"].notna().any(), "final day's spend should be visible"
