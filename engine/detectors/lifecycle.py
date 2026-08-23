"""Lifecycle detectors: whether customers come back, and whether email brings them.

Both detectors here exist to make the same distinction from opposite sides. The
email one must NOT fire commercially on a measurement artifact; the retention one
MUST fire on a real collapse that a censoring guard could easily hide. Getting
either backwards is the failure this project is built to avoid.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine.detectors import register
from engine.signals import Classification, Direction, Signal, Tier
from engine.stats import mann_kendall, theil_sen_slope


@register("email_engagement_decay")
def email_engagement_decay(ctx) -> list[Signal]:
    """Falling engagement -- but only commercial if conversion falls with it.

    Open rate is a compromised metric: privacy proxies inflate it and the
    denominator drifts as a list ages. So a decline in opens on its own says
    nothing about the business. The discriminating question is whether the
    downstream commercial metric co-moved.

    Opens down, conversion flat  -> ARTIFACT. Do not act.
    Opens down, conversion down  -> COMMERCIAL. The programme really is decaying.

    This is the same shape of judgement as the Meta case, where CTR fell but
    cost per click rose, making it commercial on cost-side evidence.
    """
    cfg = ctx.detector_config("email_engagement_decay")
    if not cfg.get("enabled", True) or ctx.email.empty:
        return []

    min_weeks = cfg.get("min_weeks", 8)
    decline_gate = cfg.get("min_open_rate_decline_pct", 10.0)
    comovement_gate = cfg.get("comovement_ratio", 0.5)

    email = ctx.email.copy()
    email["week_start"] = pd.to_datetime(email["week_start"])
    signals: list[Signal] = []

    def window_change_pct(series: pd.Series) -> float:
        """Theil-Sen slope expressed as % change across the whole window.

        Robust to the December spike, and uses every point -- comparing the
        first half's mean against the second half's would average the drift
        away and read -8% where the trend is -20%.
        """
        if len(series) < 3:
            return float("nan")
        median = float(np.median(series))
        if median <= 0:
            return float("nan")
        return 100.0 * theil_sen_slope(series) * (len(series) - 1) / median

    for flow, rows in email.groupby("flow_name"):
        rows = rows.sort_values("week_start")
        # The 8-week trailing means average over fewer weeks than the label
        # implies for the first 7 weeks; drop them rather than trend them.
        rows = rows.iloc[7:]
        if len(rows) < min_weeks:
            continue

        opens = rows["open_rate"].dropna().astype(float)
        conversions = rows["conversion_rate"].dropna().astype(float)
        if len(opens) < min_weeks or len(conversions) < min_weeks:
            continue

        open_change = window_change_pct(opens)
        conv_change = window_change_pct(conversions)
        if not np.isfinite(open_change) or not np.isfinite(conv_change):
            continue
        if open_change > -decline_gate:
            continue    # not falling enough to be worth a word

        ratio = (conv_change / open_change) if open_change < 0 else 0.0
        conversion_followed = conv_change < 0 and ratio >= comovement_gate

        classification = (
            Classification.COMMERCIAL if conversion_followed else Classification.ARTIFACT
        )
        reading = (
            "opens and conversion falling together - the programme is decaying"
            if conversion_followed else
            "opens falling, conversion holding - measurement artifact, do not act"
        )

        _, p = mann_kendall(opens)
        signals.append(Signal(
            detector="email_engagement_decay", entity_type="flow", entity_id=str(flow),
            as_of_date=ctx.as_of, fired_date=ctx.window_end,
            severity=float(min(abs(open_change) / 40.0, 1.0)),
            direction=Direction.DEGRADING,
            classification=classification,
            attribution_tier=Tier.B,
            p_value=float(p) if np.isfinite(p) else None,
            evidence={
                "open_rate_change_pct": round(open_change, 2),
                "conversion_rate_change_pct": round(conv_change, 2),
                "comovement_ratio": round(ratio, 3),
                "comovement_threshold": comovement_gate,
                "conversion_followed": bool(conversion_followed),
                "open_rate_latest": round(float(opens.iloc[-1]), 4),
                "conversion_rate_latest": round(float(conversions.iloc[-1]), 4),
                "weeks": int(len(opens)),
                "reading": reading,
            },
        ))
    return signals


@register("cohort_retention_shift")
def cohort_retention_shift(ctx) -> list[Signal]:
    """Retention falling across acquisition cohorts, compared at equal age.

    Two failure modes this has to avoid, in opposite directions:

    Firing on censoring. A cohort acquired last month has not failed to return;
    it has not been asked yet. Only cohorts whose 90-day window has fully closed
    are compared, and the mart publishes NULL for the rest.

    Staying silent on the real thing. The blended monthly repeat rate sits near
    24% all year and looks healthy, because the 2024 cohorts are still buying.
    Underneath, the 90-day repeat rate runs 31.8% -> 0.0% across cohorts that
    are ALL fully exposed. That is measured, and it must fire.

    Tier B: cohort membership needs no channel attribution.
    """
    cfg = ctx.detector_config("cohort_retention_shift")
    if not cfg.get("enabled", True) or ctx.retention.empty:
        return []

    min_size = cfg.get("min_cohort_size", 100)
    min_decline = cfg.get("min_decline_pct", 25.0)
    min_cohorts = cfg.get("min_cohorts", 4)

    # One row per cohort: repeat_rate_90d repeats across every months_since row.
    cohorts = (
        ctx.retention[
            ctx.retention["has_full_90d_exposure"].astype(bool)
            & ctx.retention["repeat_rate_90d"].notna()
            & (ctx.retention["cohort_size"] >= min_size)
        ]
        .groupby("cohort_month", as_index=False)
        .agg(repeat_rate_90d=("repeat_rate_90d", "first"),
             cohort_size=("cohort_size", "first"))
        .sort_values("cohort_month")
    )
    if len(cohorts) < min_cohorts:
        return []

    rates = cohorts["repeat_rate_90d"].astype(float)
    first, last = float(rates.iloc[0]), float(rates.iloc[-1])
    if first <= 0:
        return []

    decline_pct = 100.0 * (1 - last / first)
    if decline_pct < min_decline:
        return []

    slope = theil_sen_slope(rates)
    _, p = mann_kendall(rates)

    return [Signal(
        detector="cohort_retention_shift", entity_type="cohort", entity_id="all",
        as_of_date=ctx.as_of, fired_date=ctx.window_end,
        severity=float(min(decline_pct / 100.0, 1.0)),
        direction=Direction.DEGRADING,
        classification=Classification.COMMERCIAL,
        attribution_tier=Tier.B,
        p_value=float(p) if np.isfinite(p) else None,
        evidence={
            "first_cohort": str(cohorts["cohort_month"].iloc[0])[:10],
            "last_cohort": str(cohorts["cohort_month"].iloc[-1])[:10],
            "first_rate_pct": round(100 * first, 2),
            "last_rate_pct": round(100 * last, 2),
            "decline_pct": round(decline_pct, 1),
            "fully_exposed_cohorts": int(len(cohorts)),
            "theil_sen_slope": round(float(slope), 5),
            "series_pct": [round(100 * float(r), 2) for r in rates],
            "note": ("all cohorts compared are fully exposed - this is measured "
                     "decline, not censoring"),
        },
    )]
