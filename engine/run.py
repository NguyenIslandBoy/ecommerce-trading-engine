"""Runs every detector at a cursor and adjudicates what they produce.

Detectors deliberately do not decide whether they should fire. Three things
happen to their output here, in order, and all three are the difference between
a list of anomalies and something worth acting on:

1. Data-quality suppression. A signal whose evidence overlaps a source outage is
   reclassified DATA_QUALITY, however large it looks. Missing spend makes cost
   metrics improve, so this is the step that stops the engine recommending more
   budget for a channel that merely stopped reporting.

2. FDR control. Roughly 9 detectors across ~20 entities at every one of 365
   cursors manufactures false positives by construction. Benjamini-Hochberg
   bounds the share of fired signals that are noise; Bonferroni at this scale
   would reject everything real along with them.

3. Confidence. Severity discounted by attribution reliability and by how clean
   the window was. A Tier C signal cannot exceed 0.55, which is what keeps
   last-click evidence below the autonomy threshold in Layer 3.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from engine.context import Context, Warehouse
from engine.detectors import REGISTRY
from engine.signals import Classification, Signal


def detect(ctx: Context) -> list[Signal]:
    """Every enabled detector, adjudicated. Order of the three passes matters."""
    raw: list[Signal] = []
    for name, detector in REGISTRY.items():
        if not ctx.detector_config(name).get("enabled", True):
            continue
        raw.extend(detector(ctx))

    adjudicated = _suppress_over_outages(raw, ctx)
    return _apply_fdr(adjudicated, ctx)


def _suppress_over_outages(signals: list[Signal], ctx: Context) -> list[Signal]:
    outage_windows: list[tuple[dt.date, dt.date, str]] = []
    for signal in signals:
        if signal.detector != "data_completeness":
            continue
        outage_windows.append((
            dt.date.fromisoformat(signal.evidence["suppression_from"]),
            dt.date.fromisoformat(signal.evidence["suppression_to"]),
            signal.entity_id,
        ))

    if not outage_windows:
        return signals

    evidence_days = ctx.config["trend"]["window_days"]
    out: list[Signal] = []
    for signal in signals:
        if signal.detector == "data_completeness":
            out.append(signal)
            continue

        window_start = signal.fired_date - dt.timedelta(days=evidence_days)
        overlapping = [
            source for start, end, source in outage_windows
            if window_start <= end and signal.fired_date >= start
        ]
        if not overlapping:
            out.append(signal)
            continue

        evidence = dict(signal.evidence)
        evidence["suppressed_by"] = sorted(set(overlapping))
        evidence["suppression_reason"] = (
            "evidence window overlaps a source outage; the movement cannot be "
            "separated from the missing data"
        )
        out.append(Signal(
            detector=signal.detector, entity_type=signal.entity_type,
            entity_id=signal.entity_id, as_of_date=signal.as_of_date,
            fired_date=signal.fired_date, severity=signal.severity,
            direction=signal.direction, evidence=evidence,
            classification=Classification.DATA_QUALITY,
            attribution_tier=signal.attribution_tier,
            data_quality_score=0.3,
            p_value=signal.p_value, passed_fdr=signal.passed_fdr,
        ))
    return out


def _apply_fdr(signals: list[Signal], ctx: Context) -> list[Signal]:
    from engine.stats import benjamini_hochberg

    alpha = ctx.config.get("fdr", {}).get("alpha", 0.10)
    testable = [s for s in signals if s.p_value is not None and np.isfinite(s.p_value)]
    if not testable:
        return signals

    keep = benjamini_hochberg([s.p_value for s in testable], alpha)
    verdict = {id(s): bool(k) for s, k in zip(testable, keep)}

    out: list[Signal] = []
    for signal in signals:
        passed = verdict.get(id(signal), True)
        if passed == signal.passed_fdr:
            out.append(signal)
            continue
        out.append(Signal(
            detector=signal.detector, entity_type=signal.entity_type,
            entity_id=signal.entity_id, as_of_date=signal.as_of_date,
            fired_date=signal.fired_date, severity=signal.severity,
            direction=signal.direction, evidence=signal.evidence,
            classification=signal.classification,
            attribution_tier=signal.attribution_tier,
            data_quality_score=signal.data_quality_score,
            p_value=signal.p_value, passed_fdr=passed,
        ))
    return out


def signals_frame(signals: list[Signal]) -> pd.DataFrame:
    if not signals:
        return pd.DataFrame(columns=[
            "signal_id", "detector", "entity_type", "entity_id", "as_of_date",
            "fired_date", "severity", "confidence", "direction", "classification",
            "attribution_tier", "data_quality_score", "p_value", "passed_fdr",
            "is_actionable", "evidence",
        ])
    frame = pd.DataFrame([s.to_row() for s in signals])
    return frame.sort_values(
        ["is_actionable", "confidence"], ascending=[False, False]
    ).reset_index(drop=True)


def run_at(cursor) -> pd.DataFrame:
    """Convenience entry point: load the warehouse and detect at one cursor."""
    return signals_frame(detect(Warehouse().at(cursor)))


if __name__ == "__main__":
    import sys

    warehouse = Warehouse()
    cursor = sys.argv[1] if len(sys.argv) > 1 else str(warehouse.latest_cursor)
    frame = signals_frame(detect(warehouse.at(cursor)))

    print(f"\ncursor {cursor} -- {len(frame)} signals\n")
    if frame.empty:
        print("  none")
    else:
        columns = ["detector", "entity_id", "classification", "attribution_tier",
                   "confidence", "is_actionable"]
        print(frame[columns].to_string(index=False))
        print("\nActionable:")
        for _, row in frame[frame["is_actionable"]].iterrows():
            print(f"\n  {row['detector']} / {row['entity_id']}  "
                  f"(confidence {row['confidence']}, tier {row['attribution_tier']})")
            for key, value in row["evidence"].items():
                print(f"      {key}: {value}")
