"""Replay the engine across every cursor and score it against hand labels.

The point-in-time reconstruction makes this cheap: ~104ms a cursor, so a full
365-day replay takes under a minute, against roughly 3.5 hours if each cursor
needed a real dbt rebuild. scripts/verify_pit.py is what makes that shortcut
legitimate rather than convenient.

What is scored:

  recall     how many labelled events the engine found at all
  lead time  days from an event's onset to the first cursor that fired on it
  precision  share of fired COMMERCIAL signals that map to a labelled event
  traps      COMMERCIAL signals fired against something labelled as NOT
             commercial -- the number that matters most here

Read config/ground_truth.yml before reading any score from this: the labels are
the author's own, so this measures internal consistency, not external validity.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from engine.context import Warehouse
from engine.run import detect, signals_frame
from engine.signals import Classification

CONFIG = Path(__file__).resolve().parents[1] / "config"


def load_ground_truth() -> dict:
    with open(CONFIG / "ground_truth.yml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _as_date(value) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return pd.Timestamp(value).date()


@dataclass
class Replay:
    signals: pd.DataFrame
    cursors: list[dt.date]
    seconds: float


def replay(warehouse: Warehouse | None = None, step_days: int = 1,
           start: dt.date | None = None, end: dt.date | None = None,
           progress=None) -> Replay:
    """Run detection at every cursor in the range and stack the results."""
    import time

    warehouse = warehouse or Warehouse()
    # Detectors need history before they can say anything; starting at day one
    # would score the engine on cursors where silence is the only correct answer.
    first = start or (warehouse.first_day + dt.timedelta(days=120))
    last = end or warehouse.last_day

    cursors: list[dt.date] = []
    day = first
    while day <= last:
        cursors.append(day)
        day += dt.timedelta(days=step_days)

    began = time.time()
    frames: list[pd.DataFrame] = []
    for index, cursor in enumerate(cursors):
        frame = signals_frame(detect(warehouse.at(cursor)))
        if not frame.empty:
            frame = frame.copy()
            frame["cursor"] = cursor
            frames.append(frame)
        if progress is not None:
            progress(index + 1, len(cursors))

    stacked = (pd.concat(frames, ignore_index=True) if frames
               else signals_frame([]).assign(cursor=pd.Series(dtype="object")))
    return Replay(signals=stacked, cursors=cursors, seconds=time.time() - began)


def _matches(row, event: dict) -> bool:
    if row["detector"] != event["detector"]:
        return False
    wanted = event.get("entity_id")
    if wanted and wanted != "any" and row["entity_id"] != wanted:
        return False
    contains = event.get("entity_contains")
    if contains and contains.lower() not in str(row["entity_id"]).lower():
        return False
    return True


def score(replay_result: Replay, truth: dict | None = None) -> dict:
    truth = truth or load_ground_truth()
    signals = replay_result.signals
    events = truth.get("events", [])
    traps = truth.get("traps", [])

    event_rows = []
    matched_ids: set[str] = set()

    for event in events:
        onset = _as_date(event["onset"])
        wanted_class = event.get("must_classify", Classification.COMMERCIAL.value)

        if signals.empty:
            hits = signals
        else:
            hits = signals[signals.apply(lambda r: _matches(r, event), axis=1)]
            hits = hits[hits["classification"] == wanted_class]
            # Only count a fire that survived FDR; a suppressed signal is not a
            # detection, it is a thing the engine decided not to say.
            hits = hits[hits["passed_fdr"]]
            # A fire well before onset is not early detection of THIS event, it
            # is an unrelated anomaly that happens to share an entity. Counting
            # it produced a "-66 day lead" on the D3 breakout from a November
            # blip. Fires outside the grace window still count against
            # precision, which is where they belong.
            grace = dt.timedelta(days=truth.get("onset_grace_days", 14))
            hits = hits[hits["cursor"].map(_as_date) >= onset - grace]

        if hits.empty:
            event_rows.append({
                "event": event["id"], "detector": event["detector"],
                "onset": onset, "first_fire": None, "lead_days": None,
                "cursors_fired": 0, "detected": False,
            })
            continue

        first_fire = min(_as_date(c) for c in hits["cursor"])
        matched_ids.update(hits["signal_id"].tolist())
        event_rows.append({
            "event": event["id"], "detector": event["detector"],
            "onset": onset, "first_fire": first_fire,
            "lead_days": (first_fire - onset).days,
            "cursors_fired": int(hits["cursor"].nunique()),
            "detected": True,
        })

    trap_rows = []
    for trap in traps:
        start = _as_date(trap["from"]) if trap.get("from") else None
        finish = _as_date(trap["to"]) if trap.get("to") else None
        forbidden = trap.get("must_not_classify", Classification.COMMERCIAL.value)

        if signals.empty:
            violations = signals
        else:
            rows = signals[signals["classification"] == forbidden]
            rows = rows[rows["passed_fdr"]]
            if trap["detector"] != "any":
                rows = rows[rows["detector"] == trap["detector"]]
            if start is not None:
                rows = rows[rows["cursor"].map(_as_date) >= start]
            if finish is not None:
                rows = rows[rows["cursor"].map(_as_date) <= finish]
            violations = rows

        # A trap about manufactured signals should not count the real ones.
        # matched_ids holds every signal that mapped to a labelled event.
        if trap.get("exclude_labelled_events") and not violations.empty:
            violations = violations[~violations["signal_id"].isin(matched_ids)]

        # The seasonality trap allows a budget: the engine is expected to have
        # real things to say in January, just not a manufactured crisis.
        budget = trap.get("max_commercial_signals_per_cursor")
        if budget is not None and not violations.empty:
            per_cursor = violations.groupby("cursor").size()
            over = per_cursor[per_cursor > budget]
            violations = violations[violations["cursor"].isin(over.index)]

        trap_rows.append({
            "trap": trap["id"], "detector": trap["detector"],
            "violations": int(len(violations)),
            "cursors_violated": int(violations["cursor"].nunique()) if len(violations) else 0,
            "clean": bool(len(violations) == 0),
        })

    commercial = (signals[(signals["classification"] == Classification.COMMERCIAL.value)
                          & signals["passed_fdr"]]
                  if not signals.empty else signals)
    unique_commercial = (set(commercial["signal_id"]) if not commercial.empty else set())

    events_frame = pd.DataFrame(event_rows)
    traps_frame = pd.DataFrame(trap_rows)
    detected = int(events_frame["detected"].sum()) if not events_frame.empty else 0

    return {
        "events": events_frame,
        "traps": traps_frame,
        "recall": detected / len(events) if events else float("nan"),
        "precision": (len(matched_ids) / len(unique_commercial)
                      if unique_commercial else float("nan")),
        "trap_violations": int(traps_frame["violations"].sum()) if not traps_frame.empty else 0,
        "cursors": len(replay_result.cursors),
        "seconds": replay_result.seconds,
        "total_signals": int(len(signals)),
    }


def main() -> None:
    warehouse = Warehouse()

    def tick(done, total):
        if done % 50 == 0 or done == total:
            print(f"  {done}/{total} cursors", end="\r")

    print("Replaying every cursor...")
    result = replay(warehouse, progress=tick)
    print(f"\n{result.cursors[0]} to {result.cursors[-1]}  "
          f"({len(result.cursors)} cursors in {result.seconds:.1f}s, "
          f"{1000 * result.seconds / len(result.cursors):.0f}ms each)\n")

    report = score(result)

    print("EVENTS -- did the engine find what profiling said was there")
    events = report["events"].copy()
    events["lead_days"] = events["lead_days"].map(
        lambda v: "-" if pd.isna(v) else f"{int(v):+d}"
    )
    print(events.to_string(index=False))

    print("\nTRAPS -- did it stay quiet about what profiling said was not")
    print(report["traps"].to_string(index=False))

    print(f"\nrecall            {report['recall']:.0%}  "
          f"({int(report['events']['detected'].sum())}/{len(report['events'])} events)")
    print(f"precision         {report['precision']:.0%}  "
          f"of distinct COMMERCIAL signals map to a labelled event")
    print(f"trap violations   {report['trap_violations']}")
    print(f"signals emitted   {report['total_signals']:,} across "
          f"{report['cursors']} cursors")
    print("\nGround-truth labels are the author's own (config/ground_truth.yml).")
    print("This measures internal consistency, not external validity.")


if __name__ == "__main__":
    main()
