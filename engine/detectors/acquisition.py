"""Acquisition-cost detectors: is it getting more expensive, and why."""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine.detectors import register
from engine.signals import Direction, Signal, Tier
from engine.stats import (
    deseasonalise_dow,
    mann_kendall,
    severity_from_z,
    theil_sen_slope,
)

COST_CHANNELS = ("google", "meta")


def _trailing(frame: pd.DataFrame, days: int) -> pd.DataFrame:
    """Last ``days`` calendar days of a date-indexed frame."""
    if frame.empty:
        return frame
    cutoff = pd.Timestamp(frame["date_day"].max()) - pd.Timedelta(days=days - 1)
    window = frame[pd.to_datetime(frame["date_day"]) >= cutoff]
    # Explicit, even though the Context already sorts. Every caller feeds this
    # into a sequence-dependent estimator, so the ordering is a correctness
    # requirement of this function rather than an assumption about its input.
    return window.sort_values(["date_day", "channel"])


def _last_days_with_spend(frame: pd.DataFrame, days: int) -> pd.DataFrame | None:
    """The most recent ``days`` CONSECUTIVE days that actually have cost data.

    Not the last N calendar days. Ad spend lands a day after the event, so a
    trailing-N-calendar-day window contains only N-1 days of cost at every
    cursor except the one sitting a day past the last order. Counting calendar
    days made this detector structurally unable to fire during a backtest --
    it scored zero across 245 cursors while a qualifying run existed in the data.

    Returns None if there are not ``days`` of spend, or if the most recent ones
    are not consecutive (an outage inside the window means "5 consecutive days"
    was never observed, whatever the surviving rows look like).
    """
    rows = frame.dropna(subset=["ad_spend"]).sort_values("date_day")
    rows = rows[rows["new_customers"] > 0]
    if len(rows) < days:
        return None

    rows = rows.tail(days)
    span = pd.to_datetime(rows["date_day"])
    if (span.max() - span.min()).days != days - 1:
        return None
    return rows


def _breach_severity(ratio: float, gate: float) -> float:
    """Crossing the line is itself the event; the margin past it is secondary.

    Scored as excess-over-threshold, a CAC 20%% above LTV read 0.20 -- which
    understates "every new customer from this channel is bought at a loss".
    A bare breach now starts at 0.5 and reaches 1.0 at twice the threshold.
    """
    if gate <= 0:
        return 0.0
    return float(min(0.5 + (ratio / gate - 1.0) / 2.0, 1.0))


@register("cac_ltv_breach")
def cac_ltv_breach(ctx) -> list[Signal]:
    """Cost to acquire a customer exceeding the value that customer returns.

    The reference LTV is the most recent cohort whose horizon has fully closed.
    Using a censored cohort would compare a complete cost against an incomplete
    value and manufacture a breach out of nothing but elapsed time.
    """
    cfg = ctx.detector_config("cac_ltv_breach")
    if not cfg.get("enabled", True) or ctx.daily.empty:
        return []

    horizon = cfg.get("horizon_days", 60)
    exposed = ctx.ltv[
        (ctx.ltv["horizon_days"] == horizon) & ctx.ltv["has_full_exposure"].astype(bool)
    ]
    if exposed.empty:
        return []   # nothing has finished its window yet; silence is correct

    newest = exposed["cohort_month"].max()
    reference = exposed[exposed["cohort_month"] == newest]
    cohort_total = float(reference["cohort_size"].sum())
    if cohort_total <= 0:
        return []
    ltv = float(
        (reference["ltv_margin"].astype(float) * reference["cohort_size"]).sum()
        / cohort_total
    )
    if not np.isfinite(ltv) or ltv <= 0:
        return []

    persistence = cfg.get("persistence_days", 5)
    ratio_gate = cfg.get("breach_ratio", 1.0)
    min_customers = cfg.get("min_new_customers", 20)
    signals: list[Signal] = []

    # Blended first: attribution-free, so it can carry a decision on its own.
    daily_totals = ctx.daily.groupby("date_day", as_index=False).agg(
        ad_spend=("ad_spend", "sum"),
        new_customers=("new_customers", "sum"),
        complete=("ad_spend_is_complete", "max"),
    )
    daily_totals = daily_totals[daily_totals["complete"].astype(bool)]
    blended = _last_days_with_spend(daily_totals, persistence)
    if blended is not None and blended["new_customers"].sum() >= min_customers:
        cac = float(blended["ad_spend"].sum() / blended["new_customers"].sum())
        per_day = (blended["ad_spend"].astype(float)
                   / blended["new_customers"].astype(float))
        if bool((per_day / ltv > ratio_gate).all()):
            ratio = cac / ltv
            signals.append(Signal(
                detector="cac_ltv_breach", entity_type="account", entity_id="blended",
                as_of_date=ctx.as_of, fired_date=ctx.window_end,
                severity=_breach_severity(ratio, ratio_gate),
                direction=Direction.DEGRADING,
                attribution_tier=Tier.B,
                evidence={
                    "cac": round(cac, 2),
                    "ltv_margin_60d": round(ltv, 2),
                    "ratio": round(ratio, 3),
                    "reference_cohort": str(newest)[:10],
                    "consecutive_days": int(len(blended)),
                    "window": (f"{blended['date_day'].min()} to "
                               f"{blended['date_day'].max()}"),
                },
            ))

    for channel in COST_CHANNELS:
        rows = _last_days_with_spend(
            ctx.daily[ctx.daily["channel"] == channel], persistence)
        if rows is None or rows["new_customers"].sum() < min_customers:
            continue
        cac = float(rows["ad_spend"].sum() / rows["new_customers"].sum())
        per_day = rows["ad_spend"].astype(float) / rows["new_customers"].astype(float)
        if not bool((per_day / ltv > ratio_gate).all()):
            continue
        ratio = cac / ltv
        signals.append(Signal(
            detector="cac_ltv_breach", entity_type="channel", entity_id=channel,
            as_of_date=ctx.as_of, fired_date=ctx.window_end,
            severity=_breach_severity(ratio, ratio_gate),
            direction=Direction.DEGRADING,
            attribution_tier=Tier.C,     # channel CAC depends on last-click
            evidence={
                "cac": round(cac, 2),
                "ltv_margin_60d": round(ltv, 2),
                "ratio": round(ratio, 3),
                "reference_cohort": str(newest)[:10],
                "consecutive_days": int(len(rows)),
                "window": f"{rows['date_day'].min()} to {rows['date_day'].max()}",
            },
        ))
    return signals


def _trend_signal(series: pd.DataFrame, value_col: str, cfg: dict, ctx,
                  entity_type: str, entity_id: str, tier: Tier) -> Signal | None:
    min_points = ctx.config["trend"]["min_points"]
    if len(series) < min_points:
        return None

    adjusted, _ = deseasonalise_dow(series, value_col)
    adjusted = pd.Series(adjusted).dropna()
    if len(adjusted) < min_points:
        return None

    median = float(np.median(adjusted))
    if median <= 0:
        return None

    slope = theil_sen_slope(adjusted)
    z, p = mann_kendall(adjusted)
    if not np.isfinite(slope) or not np.isfinite(p):
        return None

    pct_per_day = 100.0 * slope / median
    if abs(pct_per_day) < cfg.get("min_pct_per_day", 0.15):
        return None
    if p > cfg.get("max_p_value", 0.05):
        return None

    return Signal(
        detector="cac_trend", entity_type=entity_type, entity_id=entity_id,
        as_of_date=ctx.as_of, fired_date=ctx.window_end,
        severity=severity_from_z(z),
        direction=Direction.DEGRADING if slope > 0 else Direction.IMPROVING,
        attribution_tier=tier, p_value=float(p),
        evidence={
            "pct_per_day": round(pct_per_day, 3),
            "pct_over_window": round(pct_per_day * len(adjusted), 2),
            "theil_sen_slope": round(float(slope), 4),
            "mann_kendall_z": round(float(z), 3),
            "window_days": int(len(adjusted)),
            "window_median": round(median, 2),
        },
    )


@register("cac_trend")
def cac_trend(ctx) -> list[Signal]:
    """Direction and significance of acquisition cost over the trailing window.

    Theil-Sen rather than least squares, Mann-Kendall rather than a t-test: the
    November/December peak dominates both parametric alternatives, and would
    make a genuine trend read as noise or vice versa.
    """
    cfg = ctx.detector_config("cac_trend")
    if not cfg.get("enabled", True) or ctx.daily.empty:
        return []

    # Own window: CAC drift is slow, see config/detectors.yml for the measured
    # difference between a 28-day and a 90-day read on this same series.
    window = _trailing(ctx.daily, cfg.get("window_days",
                                          ctx.config["trend"]["window_days"]))
    signals: list[Signal] = []

    blended = (
        window[window["blended_cac"].notna()]
        .groupby(["date_day", "iso_dow"], as_index=False)["blended_cac"].first()
    )
    if not blended.empty:
        blended["blended_cac"] = blended["blended_cac"].astype(float)
        found = _trend_signal(blended, "blended_cac", cfg, ctx,
                              "account", "blended", Tier.B)
        if found:
            signals.append(found)

    for channel in COST_CHANNELS:
        rows = window[
            (window["channel"] == channel) & window["channel_cac"].notna()
        ].copy()
        if rows.empty:
            continue
        rows["channel_cac"] = rows["channel_cac"].astype(float)
        found = _trend_signal(rows, "channel_cac", cfg, ctx, "channel", channel, Tier.C)
        if found:
            signals.append(found)
    return signals


@register("cpc_decomposition")
def cpc_decomposition(ctx) -> list[Signal]:
    """Why CPC moved: auction price, or creative relevance.

    CPC = CPM / (CTR x 1000), so in logs the move splits additively into a CPM
    term and a CTR term. Which term dominates decides the action -- rising CPM
    with flat CTR means you are paying more for the same audience and the answer
    is budget or targeting; falling CTR means the creative is fatiguing. Firing
    "CPC is up" without the split invites the wrong fix at real cost.

    Tier A throughout: every input is platform-reported, no attribution involved.
    """
    cfg = ctx.detector_config("cpc_decomposition")
    if not cfg.get("enabled", True) or ctx.ads.empty:
        return []

    days = ctx.config["trend"]["window_days"]
    ads = ctx.ads.copy()
    ads["ad_date"] = pd.to_datetime(ads["ad_date"])
    latest = ads["ad_date"].max()
    recent_from = latest - pd.Timedelta(days=days - 1)
    prior_from = latest - pd.Timedelta(days=2 * days - 1)

    def rates(frame):
        spend = float(frame["spend"].sum())
        clicks = float(frame["clicks"].sum())
        impressions = float(frame["impressions"].sum())
        if min(spend, clicks, impressions) <= 0:
            return None
        return spend / clicks, 1000.0 * spend / impressions, clicks / impressions

    signals: list[Signal] = []
    for platform, rows in ads.groupby("platform"):
        recent = rows[rows["ad_date"] >= recent_from]
        prior = rows[(rows["ad_date"] >= prior_from) & (rows["ad_date"] < recent_from)]
        if recent.empty or prior.empty:
            continue

        now, before = rates(recent), rates(prior)
        if now is None or before is None:
            continue

        cpc_change = 100.0 * (now[0] / before[0] - 1)
        if abs(cpc_change) < cfg.get("min_cpc_change_pct", 10.0):
            continue

        cpm_term = float(np.log(now[1] / before[1]))
        ctr_term = float(-np.log(now[2] / before[2]))
        total = abs(cpm_term) + abs(ctr_term)
        if total == 0:
            continue

        gate = cfg.get("dominance_ratio", 1.5)
        if abs(cpm_term) >= gate * abs(ctr_term):
            driver = "cpm"
            reading = "auction price rising - budget or targeting, not creative"
        elif abs(ctr_term) >= gate * abs(cpm_term):
            driver = "ctr"
            reading = "click-through falling - creative fatigue"
        else:
            driver = "both"
            reading = "auction and creative contributing roughly equally"

        signals.append(Signal(
            detector="cpc_decomposition", entity_type="channel", entity_id=platform,
            as_of_date=ctx.as_of, fired_date=ctx.window_end,
            severity=float(min(abs(cpc_change) / 50.0, 1.0)),
            direction=Direction.DEGRADING if cpc_change > 0 else Direction.IMPROVING,
            attribution_tier=Tier.A,
            evidence={
                "cpc_change_pct": round(cpc_change, 2),
                "cpm_change_pct": round(100.0 * (now[1] / before[1] - 1), 2),
                "ctr_change_pct": round(100.0 * (now[2] / before[2] - 1), 2),
                "cpm_share_of_move": round(abs(cpm_term) / total, 3),
                "ctr_share_of_move": round(abs(ctr_term) / total, 3),
                "dominant_driver": driver,
                "reading": reading,
                "cpc_now": round(now[0], 4),
                "cpc_prior": round(before[0], 4),
            },
        ))
    return signals


@register("channel_cvr_shift")
def channel_cvr_shift(ctx) -> list[Signal]:
    """Orders per click, by channel -- the conversion step between the ad and
    the sale.

    Covers Google and Meta only, and says so. TikTok has no cost file and
    unattributed has no channel, so neither has clicks to divide by; between
    them that is 36% of orders this detector cannot see. Partial coverage of a
    real metric, stated, rather than a number invented for the missing two.

    Tier C: clicks are platform-reported and exact, but attributing the ORDERS
    to a channel is last-click, so the ratio inherits the weaker half.

    Distinct from cpc_decomposition's CTR, which is impressions to clicks. This
    is clicks to orders -- a landing-page and offer question, not an ad one.
    """
    cfg = ctx.detector_config("channel_cvr_shift")
    if not cfg.get("enabled", True) or ctx.daily.empty:
        return []

    window = _trailing(ctx.daily, cfg.get("window_days", 90))
    signals: list[Signal] = []

    for channel in COST_CHANNELS:
        rows = window[(window["channel"] == channel)
                      & window["clicks"].notna()].copy()
        rows = rows[rows["clicks"].astype(float) > 0]
        if len(rows) < ctx.config["trend"]["min_points"]:
            continue
        if rows["clicks"].astype(float).sum() < cfg.get("min_clicks", 5000):
            continue

        rows["cvr"] = (rows["orders"].astype(float)
                       / rows["clicks"].astype(float))

        adjusted, _ = deseasonalise_dow(rows, "cvr")
        adjusted = pd.Series(adjusted).dropna()
        if len(adjusted) < ctx.config["trend"]["min_points"]:
            continue

        median = float(np.median(adjusted))
        if median <= 0:
            continue

        slope = theil_sen_slope(adjusted)
        z, p = mann_kendall(adjusted)
        if not np.isfinite(slope) or not np.isfinite(p):
            continue

        pct_over_window = 100.0 * slope * (len(adjusted) - 1) / median
        if abs(pct_over_window) < cfg.get("min_change_pct", 15.0):
            continue
        if p > cfg.get("max_p_value", 0.05):
            continue

        signals.append(Signal(
            detector="channel_cvr_shift", entity_type="channel",
            entity_id=channel,
            as_of_date=ctx.as_of, fired_date=ctx.window_end,
            severity=severity_from_z(z),
            direction=Direction.IMPROVING if slope > 0 else Direction.DEGRADING,
            attribution_tier=Tier.C,
            p_value=float(p),
            evidence={
                "cvr_pct_now": round(100 * float(adjusted.iloc[-1]), 4),
                "cvr_pct_median": round(100 * median, 4),
                "pct_change_over_window": round(pct_over_window, 2),
                "mann_kendall_z": round(float(z), 3),
                "days": int(len(adjusted)),
                "coverage": ("google and meta only - tiktok has no cost file "
                             "and unattributed has no channel, so 36% of "
                             "orders are outside this measure"),
            },
        ))
    return signals
