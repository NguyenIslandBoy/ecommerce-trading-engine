"""Product and basket detectors: what is selling, what runs out, what a sale is worth."""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine.detectors import register
from engine.signals import Classification, Direction, Signal, Tier
from engine.stats import (
    mad_zscore,
    mann_kendall,
    severity_from_z,
    theil_sen_slope,
)


@register("product_velocity")
def product_velocity(ctx) -> list[Signal]:
    """Variants gaining or losing SHARE of the catalogue, not raw velocity.

    Share, not absolute units, and that distinction is the whole detector. Scored
    against its own history in raw velocity, this fired on 23 of 24 variants at
    2024-11-30 -- because in peak season every product is anomalous relative to
    its own pre-peak past, and a detector that flags the entire catalogue has
    detected nothing.

    Dividing by the catalogue total cancels the common seasonal move exactly,
    with no seasonal model to fit -- which matters here, because 12 months of
    data contains exactly one November and an annual factor is not estimable.
    What survives is genuine relative movement, which is what the finding
    actually was: Vitamin D3 tripled "while all 11 other products stayed flat".

    The first 28 days of the spine are skipped: velocity_28d averages over
    fewer real days than its name implies until the window fills, and reads
    7.0 units/day on day one from a single day of data.
    """
    cfg = ctx.detector_config("product_velocity")
    if not cfg.get("enabled", True) or ctx.product.empty:
        return []

    product = ctx.product.copy()
    product["date_day"] = pd.to_datetime(product["date_day"])

    warmup = cfg.get("warmup_days", 28)
    first_day = product["date_day"].min()
    product = product[product["date_day"] >= first_day + pd.Timedelta(days=warmup)]
    if product.empty:
        return []

    latest = product["date_day"].max()
    threshold = cfg.get("z_threshold", 3.0)
    min_units = cfg.get("min_units_28d", 20)
    signals: list[Signal] = []

    # Catalogue velocity per day. Every variant is measured as a share of this,
    # so a peak that lifts the whole catalogue cancels instead of firing.
    catalogue = (product.groupby("date_day")["velocity_7d"].sum()
                 .replace(0, np.nan))
    product = product.assign(
        share=product["velocity_7d"].astype(float)
        / product["date_day"].map(catalogue).astype(float)
    )

    for variant_id, rows in product.groupby("variant_id"):
        rows = rows.sort_values("date_day")
        current = rows[rows["date_day"] == latest]
        if current.empty:
            continue
        current = current.iloc[0]

        history = rows[rows["date_day"] < latest]["share"].dropna().astype(float)
        if len(history) < 14:
            continue

        recent_units = float(
            rows[rows["date_day"] > latest - pd.Timedelta(days=28)]["units"].sum()
        )
        if recent_units < min_units:
            continue    # long tail: noise would dominate any z-score

        point = float(current["share"]) if pd.notna(current["share"]) else None
        if point is None:
            continue

        z = mad_zscore(history, point)
        if not np.isfinite(z) or abs(z) < threshold:
            continue

        signals.append(Signal(
            detector="product_velocity", entity_type="variant",
            entity_id=str(current.get("sku") or variant_id),
            as_of_date=ctx.as_of, fired_date=ctx.window_end,
            severity=severity_from_z(z),
            direction=Direction.IMPROVING if z > 0 else Direction.DEGRADING,
            attribution_tier=Tier.A,     # units sold, no attribution involved
            evidence={
                "product_title": str(current.get("product_title", "")),
                "catalogue_share": round(point, 5),
                "catalogue_share_median": round(float(np.median(history)), 5),
                "velocity_7d": (round(float(current["velocity_7d"]), 3)
                                if pd.notna(current["velocity_7d"]) else None),
                "velocity_28d": (round(float(current["velocity_28d"]), 3)
                                 if pd.notna(current["velocity_28d"]) else None),
                "mad_z": round(float(z), 2),
                "basis": "share of catalogue velocity - cancels seasonality",
                "units_28d": int(recent_units),
            },
        ))
    return signals


@register("inventory_cover")
def inventory_cover(ctx) -> list[Signal]:
    """Variants that will run out before a reorder can plausibly land.

    Deliberately evaluated at the latest visible date only. products.csv carries
    a CURRENT stock snapshot with no history, so mart_product_daily applies
    today's inventory to every historical day -- days_of_cover is meaningless in
    the past and must never be backtested. Firing it historically would invent a
    stockout that never happened and inflate the backtest's recall.
    """
    cfg = ctx.detector_config("inventory_cover")
    if not cfg.get("enabled", True) or ctx.product.empty:
        return []
    if not cfg.get("latest_date_only", True):
        raise ValueError(
            "inventory_cover.latest_date_only cannot be disabled: inventory is a "
            "snapshot with no history, so historical cover figures are fiction."
        )

    # The reason this exists: in a 245-cursor replay this detector fired 80
    # times in January alone, using stock levels that were not measured until
    # June. Every one of those was fiction, and they were inflating the
    # backtest's signal count while claiming a seasonal crisis.
    if not ctx.is_latest:
        return []

    product = ctx.product.copy()
    product["date_day"] = pd.to_datetime(product["date_day"])
    latest = product["date_day"].max()
    rows = product[product["date_day"] == latest]

    critical = cfg.get("critical_days", 14)
    warning = cfg.get("warning_days", 28)
    signals: list[Signal] = []

    for _, row in rows.iterrows():
        cover = row.get("days_of_cover")
        if pd.isna(cover):
            continue
        cover = float(cover)
        if cover >= warning:
            continue

        severity = 1.0 if cover < critical else 0.55
        signals.append(Signal(
            detector="inventory_cover", entity_type="variant",
            entity_id=str(row.get("sku") or row["variant_id"]),
            as_of_date=ctx.as_of, fired_date=ctx.window_end,
            severity=severity,
            direction=Direction.DEGRADING,
            attribution_tier=Tier.A,
            evidence={
                "product_title": str(row.get("product_title", "")),
                "days_of_cover": round(cover, 1),
                "inventory_quantity": (int(row["inventory_quantity"])
                                       if pd.notna(row["inventory_quantity"]) else None),
                "velocity_28d": (round(float(row["velocity_28d"]), 3)
                                 if pd.notna(row["velocity_28d"]) else None),
                "band": "critical" if cover < critical else "warning",
                "snapshot_caveat": "current stock only - no inventory history exists",
            },
        ))
    return signals


@register("aov_decomposition")
def aov_decomposition(ctx) -> list[Signal]:
    """Average order value, split into the two things that can move it.

    AOV = units_per_order x revenue_per_unit. A fall caused by smaller baskets
    is a merchandising problem; one caused by lower realised price is a pricing
    or discounting problem. They call for opposite actions, so the detector
    reports which moved rather than that AOV moved.

    Tier B: revenue and order counts need no attribution.
    """
    cfg = ctx.detector_config("aov_decomposition")
    if not cfg.get("enabled", True) or ctx.daily.empty:
        return []

    days = ctx.config["trend"]["window_days"]
    daily = ctx.daily.copy()
    daily["date_day"] = pd.to_datetime(daily["date_day"])

    totals = daily.groupby("date_day", as_index=False).agg(
        orders=("orders", "sum"),
        units=("units", "sum"),
        net_revenue=("net_revenue", "sum"),
        discounts=("discounts", "sum"),
    )
    totals = totals[totals["orders"] > 0]
    if len(totals) < 2 * ctx.config["trend"]["min_points"]:
        return []

    latest = totals["date_day"].max()
    recent = totals[totals["date_day"] > latest - pd.Timedelta(days=days)]
    prior = totals[
        (totals["date_day"] > latest - pd.Timedelta(days=2 * days))
        & (totals["date_day"] <= latest - pd.Timedelta(days=days))
    ]
    if recent.empty or prior.empty:
        return []

    def parts(frame):
        orders = float(frame["orders"].sum())
        units = float(frame["units"].sum())
        revenue = float(frame["net_revenue"].sum())
        discounts = float(frame["discounts"].sum())
        if min(orders, units, revenue) <= 0:
            return None
        gross = revenue + discounts
        return (revenue / orders, units / orders, revenue / units,
                discounts / gross if gross > 0 else 0.0)

    now, before = parts(recent), parts(prior)
    if now is None or before is None:
        return []

    aov_change = 100.0 * (now[0] / before[0] - 1)
    if abs(aov_change) < cfg.get("min_change_pct", 5.0):
        return []

    # log AOV = log units_per_order + log revenue_per_unit
    basket_term = float(np.log(now[1] / before[1]))
    price_term = float(np.log(now[2] / before[2]))
    total = abs(basket_term) + abs(price_term)
    if total == 0:
        return []

    driver = "basket size" if abs(basket_term) > abs(price_term) else "realised price"

    series = recent["net_revenue"].astype(float) / recent["orders"].astype(float)
    _, p = mann_kendall(series)
    if np.isfinite(p) and p > cfg.get("max_p_value", 0.05):
        return []

    return [Signal(
        detector="aov_decomposition", entity_type="account", entity_id="blended",
        as_of_date=ctx.as_of, fired_date=ctx.window_end,
        severity=float(min(abs(aov_change) / 25.0, 1.0)),
        direction=Direction.DEGRADING if aov_change < 0 else Direction.IMPROVING,
        attribution_tier=Tier.B,
        p_value=float(p) if np.isfinite(p) else None,
        evidence={
            "aov_change_pct": round(aov_change, 2),
            "aov_now": round(now[0], 2), "aov_prior": round(before[0], 2),
            "units_per_order_change_pct": round(100.0 * (now[1] / before[1] - 1), 2),
            "revenue_per_unit_change_pct": round(100.0 * (now[2] / before[2] - 1), 2),
            "discount_rate_now": round(now[3], 4),
            "discount_rate_prior": round(before[3], 4),
            "basket_share_of_move": round(abs(basket_term) / total, 3),
            "price_share_of_move": round(abs(price_term) / total, 3),
            "dominant_driver": driver,
        },
    )]
