"""Layer 3 -- turning signals into decisions, with their uncertainty attached.

A recommendation here is not "do X". It is: do X, at this size, expecting this
distribution of outcomes, with this much autonomy, for these reasons -- and if
the distribution is close enough to a coin flip that another week of data would
settle it, the recommendation is to wait instead.

Two rules do most of the work:

  Reversibility sets the CEILING on autonomy. Ad spend can be unwound tomorrow;
  an inventory purchase cannot. So no confidence level, however high, lets an
  irreversible action execute unattended.

  Confidence sets the MAGNITUDE. In the medium band the action still happens,
  but capped small enough that being wrong is cheap. Sizing a reversible test to
  the strength of the evidence is the behaviour the whole engine exists for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from engine.context import Context
from engine.signals import Classification, Tier
from engine.simulate import (
    Outcome,
    simulate_creative_refresh,
    simulate_reallocation,
    simulate_reorder,
    value_of_information,
)

CONFIG = Path(__file__).resolve().parents[1] / "config"

MONITOR = "MONITOR"
WAIT = "WAIT"
AUTO_EXECUTE = "AUTO-EXECUTE"
AUTO_CAPPED = "AUTO-EXECUTE (capped magnitude)"
FLAG_FOR_REVIEW = "FLAG FOR REVIEW"


@dataclass
class Recommendation:
    action_type: str
    entity_id: str
    magnitude: float                 # share of budget, or units; 0 where n/a
    magnitude_unit: str
    rationale: str
    confidence: float
    reversibility: str
    autonomy: str
    source_signal_id: str
    source_detector: str
    outcome: Outcome | None = None
    voi: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_row(self) -> dict:
        row = {
            "action_type": self.action_type,
            "entity_id": self.entity_id,
            "magnitude": self.magnitude,
            "magnitude_unit": self.magnitude_unit,
            "confidence": round(self.confidence, 4),
            "reversibility": self.reversibility,
            "autonomy": self.autonomy,
            "rationale": self.rationale,
            "detector": self.source_detector,
            "signal_id": self.source_signal_id,
            "notes": "; ".join(self.notes),
        }
        if self.outcome is not None:
            row.update({
                "median_revenue_delta": (None if self.outcome.revenue_median is None
                                         else round(self.outcome.revenue_median, 2)),
                "median_margin_delta": round(self.outcome.median, 2),
                "p_positive": (None if self.outcome.p_positive is None
                               else round(self.outcome.p_positive, 3)),
                "ci80_low": round(self.outcome.ci_low, 2),
                "ci80_high": round(self.outcome.ci_high, 2),
            })
        return row


def load_actions() -> dict:
    with open(CONFIG / "actions.yml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def decide_autonomy(confidence: float, reversibility: str, tier: Tier,
                    cfg: dict, voi: dict | None = None,
                    outcome=None, action_type: str | None = None,
                    ) -> tuple[str, list[str]]:
    """The autonomy gate. Returns (decision, reasons).

    Four things can stop an action, in order of precedence: it is not really an
    action at all; its own simulation says it does not pay; another week of data
    would settle a near coin-flip; the signal is not confident enough.
    """
    rules = cfg["autonomy"]
    notes: list[str] = []

    # An investigation is a human task by definition. "Auto-executing" one is
    # meaningless, so it is always raised rather than actioned.
    if action_type == "INVESTIGATE_DATA":
        return FLAG_FOR_REVIEW, ["investigation is a human task - raised, not executed"]

    # Does the action pay? Confidence says the signal is real; this says acting
    # on it is expected to be worth it. A real signal can still have no
    # profitable response.
    if outcome is not None and outcome.p_positive is not None:
        floor = rules.get("min_p_positive", 0.55)
        if outcome.p_positive < floor:
            return MONITOR, [
                f"simulation says this does not pay: P(margin gain) = "
                f"{outcome.p_positive:.0%}, median "
                f"{outcome.median:+,.0f} - below the {floor:.0%} floor"
            ]

    if voi and voi.get("recommend_wait"):
        return WAIT, [voi["reason"]]

    if confidence < rules["medium_confidence"]:
        return MONITOR, ["confidence below the action threshold"]

    if reversibility == "irreversible":
        notes.append(
            "irreversible: capital committed cannot be unwound, so this is "
            "reviewed regardless of confidence"
        )
        return FLAG_FOR_REVIEW, notes

    if tier is Tier.C and not rules.get("tier_c_may_auto_execute", False):
        notes.append(
            "Tier C evidence: 26.8% of orders are unattributed, so last-click "
            "channel figures cannot carry an unattended decision"
        )
        return FLAG_FOR_REVIEW, notes

    if confidence >= rules["high_confidence"]:
        return AUTO_EXECUTE, notes

    notes.append(
        f"medium confidence: magnitude capped at "
        f"{rules['medium_band_magnitude_cap']:.0%} so being wrong stays cheap"
    )
    return AUTO_CAPPED, notes


def _cheapest_alternative(ctx: Context, exclude: str) -> str | None:
    """Channel with the lowest recent CAC that is not ``exclude``."""
    daily = ctx.daily.dropna(subset=["ad_spend"])
    best, best_cac = None, np.inf
    for channel, rows in daily.groupby("channel"):
        if channel == exclude:
            continue
        customers = float(rows["new_customers"].sum())
        spend = float(rows["ad_spend"].sum())
        if customers <= 0 or spend <= 0:
            continue
        cac = spend / customers
        if cac < best_cac:
            best, best_cac = str(channel), cac
    return best


def _reference_ltv_revenue(ctx: Context, horizon: int = 60) -> float | None:
    """Revenue counterpart of the margin LTV, on the same cohort."""
    exposed = ctx.ltv[
        (ctx.ltv["horizon_days"] == horizon) & ctx.ltv["has_full_exposure"].astype(bool)
    ]
    if exposed.empty:
        return None
    newest = exposed[exposed["cohort_month"] == exposed["cohort_month"].max()]
    size = float(newest["cohort_size"].sum())
    if size <= 0:
        return None
    return float(
        (newest["ltv_revenue"].astype(float) * newest["cohort_size"]).sum() / size)


def _reference_ltv(ctx: Context, horizon: int = 60) -> float | None:
    exposed = ctx.ltv[
        (ctx.ltv["horizon_days"] == horizon) & ctx.ltv["has_full_exposure"].astype(bool)
    ]
    if exposed.empty:
        return None
    newest = exposed[exposed["cohort_month"] == exposed["cohort_month"].max()]
    size = float(newest["cohort_size"].sum())
    if size <= 0:
        return None
    return float((newest["ltv_margin"].astype(float) * newest["cohort_size"]).sum() / size)


def recommend(ctx: Context, signals: pd.DataFrame) -> list[Recommendation]:
    """Recommendations for every actionable signal.

    Only COMMERCIAL signals that survived FDR reach this point. A DATA_QUALITY
    or ARTIFACT signal produces an investigation at most -- never a spend or
    inventory decision.
    """
    cfg = load_actions()
    sim_cfg = cfg["simulation"]
    actions = cfg["actions"]
    ltv = _reference_ltv(ctx)
    ltv_revenue = _reference_ltv_revenue(ctx)
    out: list[Recommendation] = []

    if signals.empty:
        return out

    for _, signal in signals.iterrows():
        mapping = actions.get(signal["detector"])
        if mapping is None:
            continue

        classification = signal["classification"]
        confidence = float(signal["confidence"])
        tier = Tier(signal["attribution_tier"])
        evidence = signal["evidence"]

        # Non-commercial signals never buy anything.
        if classification != Classification.COMMERCIAL.value:
            out.append(Recommendation(
                action_type="INVESTIGATE_DATA",
                entity_id=str(signal["entity_id"]),
                magnitude=0.0, magnitude_unit="n/a",
                rationale=(
                    f"Classified {classification}: "
                    + str(evidence.get("reading")
                          or evidence.get("suppression_reason")
                          or evidence.get("consequence")
                          or "not a commercial movement")
                ),
                confidence=confidence,
                reversibility="reversible",
                autonomy=MONITOR,
                source_signal_id=str(signal["signal_id"]),
                source_detector=str(signal["detector"]),
                notes=["no spend or inventory action - this is not a commercial signal"],
            ))
            continue

        detector = signal["detector"]
        entity = str(signal["entity_id"])

        if detector in ("cac_trend", "cpc_decomposition") and ltv:
            out.extend(_spend_recommendations(
                ctx, signal, mapping, cfg, sim_cfg, ltv, confidence, tier, entity,
                ltv_revenue,
            ))
        elif detector == "inventory_cover":
            found = _reorder_recommendation(
                ctx, signal, mapping, cfg, sim_cfg, confidence, tier, entity
            )
            if found:
                out.append(found)
        else:
            autonomy, notes = decide_autonomy(
                confidence, mapping["reversibility"], tier, cfg,
                action_type=mapping["type"],
            )
            out.append(Recommendation(
                action_type=mapping["type"], entity_id=entity,
                magnitude=0.0, magnitude_unit="n/a",
                rationale=" ".join(mapping["rationale"].split()),
                confidence=confidence,
                reversibility=mapping["reversibility"],
                autonomy=autonomy,
                source_signal_id=str(signal["signal_id"]),
                source_detector=str(detector),
                notes=notes,
            ))
    return out


def _spend_recommendations(ctx, signal, mapping, cfg, sim_cfg, ltv,
                           confidence, tier, entity,
                           ltv_revenue=None) -> list[Recommendation]:
    """Reallocation, plus a creative refresh where the CPC split justifies one."""
    results: list[Recommendation] = []
    evidence = signal["evidence"]
    donor = entity if entity in ("meta", "google") else "meta"
    receiver = _cheapest_alternative(ctx, exclude=donor)
    if receiver is None:
        return results

    shift = float(mapping.get("default_shift_pct", 0.20))

    # Where CPC decomposed, size the reallocation to the AUCTION share only.
    # Creative fatigue is fixable in place; shifting budget for it would move
    # money away from a channel whose problem was never the channel.
    if signal["detector"] == "cpc_decomposition":
        auction_share = float(evidence.get("cpm_share_of_move", 1.0))
        shift *= auction_share
        ctr_decline = abs(float(evidence.get("ctr_change_pct", 0.0))) / 100.0
        if ctr_decline > 0:
            try:
                outcome = simulate_creative_refresh(
                    ctx.daily, donor, ctr_decline, ltv,
                    draws=sim_cfg["draws"], horizon_days=sim_cfg["horizon_days"],
                    ltv_revenue=ltv_revenue,
                )
            except ValueError:
                outcome = None
            if outcome is not None:
                voi = value_of_information(outcome, sim_cfg["value_of_information_days"])
                autonomy, notes = decide_autonomy(
                    confidence, "reversible", tier, cfg, voi,
                    outcome=outcome, action_type="REFRESH_CREATIVE",
                )
                notes.append(
                    f"addresses the CTR half of the move "
                    f"({evidence.get('ctr_share_of_move')} of it)"
                )
                results.append(Recommendation(
                    action_type="REFRESH_CREATIVE",
                    entity_id=donor, magnitude=ctr_decline,
                    magnitude_unit="CTR decline to recover",
                    rationale=" ".join(mapping["rationale"].split()),
                    confidence=confidence, reversibility="reversible",
                    autonomy=autonomy,
                    source_signal_id=str(signal["signal_id"]),
                    source_detector=str(signal["detector"]),
                    outcome=outcome, voi=voi, notes=notes,
                ))

    cap = cfg["autonomy"]["medium_band_magnitude_cap"]
    shift = min(shift, float(mapping.get("max_shift_pct", 0.30)))

    try:
        outcome = simulate_reallocation(
            ctx.daily, donor, receiver, shift, margin_rate=0.7063, ltv_margin=ltv,
            draws=sim_cfg["draws"], horizon_days=sim_cfg["horizon_days"],
            ltv_revenue=ltv_revenue,
        )
    except ValueError:
        return results

    voi = value_of_information(outcome, sim_cfg["value_of_information_days"])
    autonomy, notes = decide_autonomy(
        confidence, "reversible", tier, cfg, voi,
        outcome=outcome, action_type="REALLOCATE_SPEND",
    )
    if autonomy == AUTO_CAPPED:
        shift = min(shift, cap)
    notes.append(
        f"bounded test: the cost evidence is Tier {tier.value} but the "
        f"incremental-revenue evidence is Tier C, so the shift is sized to the "
        f"weaker half"
    )

    results.append(Recommendation(
        action_type="REALLOCATE_SPEND", entity_id=f"{donor} -> {receiver}",
        magnitude=round(shift, 4), magnitude_unit="share of donor budget",
        rationale=" ".join(mapping["rationale"].split()),
        confidence=confidence, reversibility="reversible", autonomy=autonomy,
        source_signal_id=str(signal["signal_id"]),
        source_detector=str(signal["detector"]),
        outcome=outcome, voi=voi, notes=notes,
    ))
    return results


def _reorder_recommendation(ctx, signal, mapping, cfg, sim_cfg,
                            confidence, tier, entity) -> Recommendation | None:
    rows = ctx.product[ctx.product["sku"] == entity]
    if rows.empty:
        return None

    latest = rows.sort_values("date_day").iloc[-1]
    units = float(latest["units"]) if pd.notna(latest["units"]) else 0.0
    revenue = float(latest["net_revenue"]) if pd.notna(latest["net_revenue"]) else 0.0
    margin = float(latest["contribution_margin"]) if pd.notna(latest["contribution_margin"]) else 0.0
    unit_margin = (margin / units) if units > 0 else 0.0
    if unit_margin <= 0:
        # Fall back to the variant's own margin rate over its whole history.
        totals = rows[["units", "contribution_margin"]].sum()
        unit_margin = (float(totals["contribution_margin"]) / float(totals["units"])
                       if float(totals["units"]) > 0 else 0.0)
    if unit_margin <= 0:
        return None

    try:
        outcome = simulate_reorder(
            ctx.product, entity, sim_cfg["reorder_lead_time_days"], unit_margin,
            draws=sim_cfg["draws"],
        )
    except ValueError:
        return None

    voi = value_of_information(outcome, sim_cfg["value_of_information_days"])
    autonomy, notes = decide_autonomy(
        confidence, mapping["reversibility"], tier, cfg, voi,
        outcome=outcome, action_type=mapping["type"],
    )
    notes.append(
        f"P(stockout before restock) = {outcome.assumptions['p_stockout']:.0%} "
        f"over a {sim_cfg['reorder_lead_time_days']}-day lead time"
    )
    notes.append(
        "days_of_cover uses a CURRENT stock snapshot - there is no inventory "
        "history, so this is only meaningful today"
    )

    reorder_units = float(np.percentile(-outcome.draws / unit_margin, 90))
    return Recommendation(
        action_type=mapping["type"], entity_id=entity,
        magnitude=round(max(reorder_units, 0.0), 1), magnitude_unit="units short at P90",
        rationale=" ".join(mapping["rationale"].split()),
        confidence=confidence, reversibility=mapping["reversibility"],
        autonomy=autonomy,
        source_signal_id=str(signal["signal_id"]),
        source_detector=str(signal["detector"]),
        outcome=outcome, voi=voi, notes=notes,
    )


def dedupe(recommendations: list[Recommendation]) -> list[Recommendation]:
    """One recommendation per (action, entity), keeping the best-evidenced.

    cac_trend and cpc_decomposition both conclude "shift budget from Meta", and
    presenting that twice would read as two independent reasons to spend twice.
    """
    best: dict[tuple[str, str], Recommendation] = {}
    for rec in recommendations:
        key = (rec.action_type, rec.entity_id)
        current = best.get(key)
        if current is None or rec.confidence > current.confidence:
            if current is not None:
                rec.notes.append(
                    f"also raised by {current.source_detector} at confidence "
                    f"{current.confidence:.2f}"
                )
            best[key] = rec
        else:
            current.notes.append(
                f"also raised by {rec.source_detector} at confidence "
                f"{rec.confidence:.2f}"
            )
    return list(best.values())


def recommendations_frame(recommendations: list[Recommendation]) -> pd.DataFrame:
    if not recommendations:
        return pd.DataFrame(columns=[
            "action_type", "entity_id", "magnitude", "magnitude_unit", "confidence",
            "reversibility", "autonomy", "rationale", "detector", "signal_id", "notes",
        ])
    return pd.DataFrame([r.to_row() for r in recommendations])
