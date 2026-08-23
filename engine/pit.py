"""Point-in-time reconstruction of the daily metric spine.

The design spec asks the backtest to replay ``as_of_date`` across all 365 days.
A real dbt rebuild takes ~35s, so that is ~3.5 hours per run -- unusable during
development and unreproducible for anyone reviewing this.

This module reconstructs the warehouse's view at any cursor *in memory*, from a
single full build, in milliseconds. It is only legitimate because it is verified
against real rebuilds: ``scripts/verify_pit.py`` rebuilds the warehouse at
several cursors and asserts this function reproduces each one exactly.

Two rules, both owned by dbt rather than restated here:

* ``spend_available_on`` says when a day's ad spend became visible. Ads sync one
  day after the event date, so at cursor D a day's spend is knowable only when
  that column is <= D. ``assert_spend_availability_lag`` guards the uniformity.
* ``dim_date`` clamps to ``least(as_of_date, max(order_date))``, so the visible
  window never runs past the last day that actually has orders.

Cohort marts are deliberately NOT reconstructed here. A cohort's *size* changes
with the cursor -- at 2025-06-15 the June cohort is half-formed -- so truncating
a finished mart would misstate it. Cohort detectors run on real rebuilds at
month-end cursors instead: ~12 of them, which is affordable.
"""

from __future__ import annotations

import datetime as _dt

import pandas as pd

# Columns whose values come from the ad platforms and therefore arrive late.
SPEND_COLUMNS = [
    "ad_spend",
    "clicks",
    "impressions",
    "channel_cac",
    "channel_roas",
    "channel_margin_roas",
]


def _as_date(value) -> _dt.date:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    return pd.Timestamp(value).date()


def visible_window_end(daily: pd.DataFrame, cursor) -> _dt.date:
    """Last day the warehouse can see at ``cursor``.

    Mirrors ``dim_date``'s clamp: the cursor sits a day past the period close to
    absorb ingestion lag, so it must not extend the calendar past real orders.
    """
    cursor = _as_date(cursor)
    last_order_day = _as_date(daily["date_day"].max())
    return min(cursor, last_order_day)


def daily_trading_as_of(daily: pd.DataFrame, cursor) -> pd.DataFrame:
    """``mart_daily_trading`` as it stood at ``cursor``.

    Rows past the visible window are dropped. Rows inside it are kept, but any
    whose spend had not yet landed have their spend-derived measures blanked --
    the row exists with orders and no cost, which is exactly what a rebuild at
    that cursor produces, and is why the newest day of every historical rebuild
    has NULL CAC.
    """
    cursor = _as_date(cursor)
    window_end = visible_window_end(daily, cursor)

    out = daily[daily["date_day"].map(_as_date) <= window_end].copy()

    # A NULL spend_available_on means two different things, and conflating them
    # blanks half the book: TikTok and unattributed carry no ad data at ANY
    # cursor (no cost file, no channel), whereas google/meta rows simply had not
    # landed yet. Only the second kind is a lag question, so availability is
    # judged per DAY from the channels that do report spend.
    reports_spend = out["spend_available_on"].notna()
    day_landed: dict = {}
    for day, available_on in zip(
        out.loc[reports_spend, "date_day"].map(_as_date),
        out.loc[reports_spend, "spend_available_on"].map(_as_date),
    ):
        day_landed[day] = max(day_landed.get(day, available_on), available_on)

    stale = reports_spend & (out["spend_available_on"].map(_as_date) > cursor)
    for column in SPEND_COLUMNS:
        if column in out.columns:
            out.loc[stale, column] = pd.NA
    out.loc[stale, "spend_available_on"] = pd.NaT

    # A day whose spend has not landed cannot be a complete spend day, and a
    # blended CAC over partial spend is the fabricated-improvement bug the
    # warehouse exists to prevent. Judged per day, because blended_cac is a
    # daily total repeated across the day's channel rows. Only ever set False --
    # days already incomplete in the full spine (the two Meta gap days) stay so.
    pending = {day for day, available_on in day_landed.items() if available_on > cursor}
    if pending:
        affected = out["date_day"].map(lambda d: _as_date(d) in pending)
        out.loc[affected, "ad_spend_is_complete"] = False
        out.loc[affected, "blended_cac"] = pd.NA

    # Sorted, not merely filtered. Theil-Sen and Mann-Kendall are
    # SEQUENCE-dependent: a trend computed over a scrambled series is
    # meaningless, and DuckDB scans in parallel with no ordering guarantee. This
    # held only by luck until a shuffle test showed 14 signals becoming 12.
    return out.sort_values(["date_day", "channel"]).reset_index(drop=True)
