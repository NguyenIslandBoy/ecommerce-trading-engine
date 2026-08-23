"""Data-quality detection.

This detector is not about the business at all, and that is the point. Its
signals exist to stop the others: a source that stopped delivering makes every
cost metric that depends on it read better than reality, and a trading engine
that cannot tell "costs fell" from "the cost file did not arrive" will cheerfully
spend more on a channel it has simply stopped measuring.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from engine.detectors import register
from engine.signals import Classification, Direction, Signal, Tier


@register("data_completeness")
def data_completeness(ctx) -> list[Signal]:
    """Source-days the warehouse flagged as missing or partial.

    Always DATA_QUALITY, never commercial. Each signal also defines a
    suppression window that engine.run uses to reclassify any other detector
    whose evidence overlaps it.

    The concrete case: meta_ads_daily delivered nothing on 2025-03-15 and
    2025-03-16. Summed naively, blended CAC on those days reads GBP 6.75 and
    6.33 against a true figure near 11.50 -- a 40% understatement that looks
    exactly like good news.
    """
    cfg = ctx.detector_config("data_completeness")
    if not cfg.get("enabled", True) or ctx.quality.empty:
        return []

    flagged = ctx.quality[ctx.quality["issue_type"] != "ok"].copy()
    if flagged.empty:
        return []

    flagged["date_day"] = pd.to_datetime(flagged["date_day"])
    window = cfg.get("suppression_window_days", 7)
    signals: list[Signal] = []

    for source, rows in flagged.groupby("source_name"):
        rows = rows.sort_values("date_day")
        days = [d.date() for d in rows["date_day"]]

        # Consecutive missing days are one incident, not several.
        runs: list[list[dt.date]] = []
        for day in days:
            if runs and (day - runs[-1][-1]).days == 1:
                runs[-1].append(day)
            else:
                runs.append([day])

        for run in runs:
            issue_types = sorted(
                set(rows[rows["date_day"].dt.date.isin(run)]["issue_type"])
            )
            signals.append(Signal(
                detector="data_completeness", entity_type="source",
                entity_id=str(source),
                as_of_date=ctx.as_of, fired_date=run[-1],
                severity=float(min(len(run) / 3.0, 1.0)),
                direction=Direction.DEGRADING,
                classification=Classification.DATA_QUALITY,
                attribution_tier=Tier.A,
                evidence={
                    "missing_days": [str(d) for d in run],
                    "consecutive_days": len(run),
                    "issue_types": issue_types,
                    "suppression_from": str(run[0] - dt.timedelta(days=window)),
                    "suppression_to": str(run[-1] + dt.timedelta(days=window)),
                    "consequence": ("cost metrics over this window read better "
                                    "than reality - spend is missing, orders are not"),
                },
            ))
    return signals
