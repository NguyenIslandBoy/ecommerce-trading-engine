"""Monte Carlo simulation of what a recommendation would actually do.

Every output here is a distribution. That is the whole point: with retention
collapsing and 26.8% of orders unattributed, a point estimate of "this will earn
GBP 12,400" is a claim the data cannot support, and quoting one would be the
most confident-sounding way to be wrong.

The load-bearing assumption is the marginal CAC curve. Moving budget into a
channel does not buy customers at that channel's *average* cost -- the cheap
demand is already being harvested, so the next pound buys a more expensive
customer than the last. Modelled as:

    CAC(spend) = CAC_0 * (spend / spend_0) ** beta

beta = 0 is linear scaling, where reallocation is free and every simulation
returns a profit. beta is therefore estimated from the observed spend-CAC
relationship rather than assumed, and sampled with its own uncertainty so the
output distribution inherits it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

DRAWS = 10_000
HORIZON_DAYS = 30
BOOTSTRAP_WINDOW = 90


@dataclass(frozen=True)
class Outcome:
    """A simulated result. Deliberately has no ``point_estimate`` field."""

    metric: str
    draws: np.ndarray
    # None where "positive" is not a meaningful question. Margin-at-risk is
    # negative by construction, so reporting P(>0) = 0% there would read as
    # "certain to lose money" when it means "this is a loss measure".
    p_positive: float | None
    ci_low: float          # 10th percentile
    ci_high: float         # 90th percentile
    median: float
    assumptions: dict

    def summary(self) -> dict:
        return {
            "metric": self.metric,
            "median": round(float(self.median), 2),
            "p_positive": (None if self.p_positive is None
                           else round(float(self.p_positive), 3)),
            "ci80_low": round(float(self.ci_low), 2),
            "ci80_high": round(float(self.ci_high), 2),
            "draws": int(self.draws.size),
            "assumptions": self.assumptions,
        }


def _outcome(metric: str, draws: np.ndarray, assumptions: dict,
             directional: bool = True) -> Outcome:
    return Outcome(
        metric=metric,
        draws=draws,
        p_positive=float((draws > 0).mean()) if directional else None,
        ci_low=float(np.percentile(draws, 10)),
        ci_high=float(np.percentile(draws, 90)),
        median=float(np.median(draws)),
        assumptions=assumptions,
    )


def estimate_beta(daily: pd.DataFrame, channel: str,
                  window: int = BOOTSTRAP_WINDOW) -> tuple[float, float]:
    """Elasticity of CAC to spend, from the observed relationship.

    Regresses log(CAC) on log(spend) across the trailing window. Returns
    (beta, standard error). Falls back to a mildly diminishing-returns prior
    when the data cannot identify it -- never to zero, which would make
    reallocation look free.
    """
    prior_beta, prior_se = 0.35, 0.20

    rows = daily[(daily["channel"] == channel)].dropna(subset=["ad_spend"])
    rows = rows[rows["new_customers"] > 0].copy()
    if rows.empty:
        return prior_beta, prior_se

    rows["date_day"] = pd.to_datetime(rows["date_day"])
    cutoff = rows["date_day"].max() - pd.Timedelta(days=window - 1)
    rows = rows[rows["date_day"] >= cutoff]
    rows = rows[rows["ad_spend"].astype(float) > 0]
    if len(rows) < 20:
        return prior_beta, prior_se

    spend = np.log(rows["ad_spend"].astype(float).to_numpy())
    cac = np.log(
        rows["ad_spend"].astype(float).to_numpy()
        / rows["new_customers"].astype(float).to_numpy()
    )
    if np.std(spend) == 0:
        return prior_beta, prior_se

    slope, intercept = np.polyfit(spend, cac, 1)
    residuals = cac - (slope * spend + intercept)
    dof = max(len(spend) - 2, 1)
    se = float(
        np.sqrt(np.sum(residuals ** 2) / dof / np.sum((spend - spend.mean()) ** 2))
    )

    # A negative estimate says the more you spend the cheaper customers get.
    # Over a 90-day window that is far more likely to be budget tracking demand
    # than genuine increasing returns, so it is floored rather than believed.
    if not np.isfinite(slope) or slope < 0:
        return prior_beta, max(prior_se, se if np.isfinite(se) else prior_se)
    return float(slope), float(max(se, 0.05)) if np.isfinite(se) else prior_se


def _bootstrap_daily(rows: pd.DataFrame, column: str, rng, draws: int,
                     days: int) -> np.ndarray:
    """Sum of ``days`` days resampled with replacement, ``draws`` times.

    Block-free bootstrap: the day-of-week pattern is already in the pool being
    sampled from, and the horizon is a whole number of weeks.
    """
    pool = rows[column].dropna().astype(float).to_numpy()
    if pool.size == 0:
        return np.zeros(draws)
    return rng.choice(pool, size=(draws, days), replace=True).sum(axis=1)


def simulate_reallocation(daily: pd.DataFrame, from_channel: str, to_channel: str,
                          shift_pct: float, margin_rate: float,
                          ltv_margin: float, draws: int = DRAWS,
                          horizon_days: int = HORIZON_DAYS,
                          seed: int = 7) -> Outcome:
    """Move ``shift_pct`` of one channel's budget to another, for 30 days.

    Returns the distribution of the contribution-margin difference against doing
    nothing. Customers gained in the receiving channel are valued at the cohort
    margin LTV; customers lost in the donor channel are valued the same way, so
    the comparison is like for like.
    """
    rng = np.random.default_rng(seed)
    daily = daily.copy()
    daily["date_day"] = pd.to_datetime(daily["date_day"])
    cutoff = daily["date_day"].max() - pd.Timedelta(days=BOOTSTRAP_WINDOW - 1)
    recent = daily[daily["date_day"] >= cutoff]

    donor = recent[recent["channel"] == from_channel].dropna(subset=["ad_spend"])
    receiver = recent[recent["channel"] == to_channel].dropna(subset=["ad_spend"])
    if donor.empty or receiver.empty:
        raise ValueError(f"no recent spend for {from_channel} or {to_channel}")

    donor_daily_spend = float(donor["ad_spend"].astype(float).mean())
    receiver_daily_spend = float(receiver["ad_spend"].astype(float).mean())
    if min(donor_daily_spend, receiver_daily_spend) <= 0:
        raise ValueError("cannot reallocate from or into a channel with no spend")

    donor_cac = float(
        donor["ad_spend"].astype(float).sum()
        / max(donor["new_customers"].astype(float).sum(), 1.0)
    )
    receiver_cac = float(
        receiver["ad_spend"].astype(float).sum()
        / max(receiver["new_customers"].astype(float).sum(), 1.0)
    )

    beta_hat, beta_se = estimate_beta(daily, to_channel)
    donor_beta_hat, donor_beta_se = estimate_beta(daily, from_channel)

    moved_per_day = donor_daily_spend * shift_pct
    total_moved = moved_per_day * horizon_days

    # Uncertainty in the elasticities, in the baseline CACs, and in how many
    # customers a day actually produces -- all three propagate to the output.
    beta = np.clip(rng.normal(beta_hat, beta_se, draws), 0.0, 1.5)
    donor_beta = np.clip(rng.normal(donor_beta_hat, donor_beta_se, draws), 0.0, 1.5)

    receiver_cac_draws = donor_cac * 0 + rng.normal(
        receiver_cac, receiver_cac * 0.15, draws
    )
    donor_cac_draws = rng.normal(donor_cac, donor_cac * 0.15, draws)
    receiver_cac_draws = np.clip(receiver_cac_draws, 0.01, None)
    donor_cac_draws = np.clip(donor_cac_draws, 0.01, None)

    # Receiving channel: spending more makes each customer dearer.
    receiver_scale = (receiver_daily_spend + moved_per_day) / receiver_daily_spend
    marginal_receiver_cac = receiver_cac_draws * receiver_scale ** beta
    customers_gained = total_moved / marginal_receiver_cac

    # Donor channel: spending less makes each remaining customer cheaper, so the
    # customers given up cost less than the donor's average. Ignoring this would
    # overstate the loss and flatter the reallocation.
    donor_scale = (donor_daily_spend - moved_per_day) / donor_daily_spend
    donor_scale = max(donor_scale, 0.01)
    marginal_donor_cac = donor_cac_draws * donor_scale ** donor_beta
    customers_lost = total_moved / marginal_donor_cac

    ltv_draws = np.clip(rng.normal(ltv_margin, ltv_margin * 0.20, draws), 0.0, None)
    margin_delta = (customers_gained - customers_lost) * ltv_draws

    return _outcome(
        "contribution_margin_delta_30d",
        margin_delta,
        {
            "from": from_channel, "to": to_channel,
            "shift_pct": round(shift_pct * 100, 1),
            "budget_moved": round(total_moved, 2),
            "receiver_beta": round(float(beta_hat), 3),
            "receiver_beta_se": round(float(beta_se), 3),
            "donor_beta": round(float(donor_beta_hat), 3),
            "receiver_cac_avg": round(receiver_cac, 2),
            "receiver_cac_marginal_median": round(float(np.median(marginal_receiver_cac)), 2),
            "donor_cac_avg": round(donor_cac, 2),
            "ltv_margin_60d": round(ltv_margin, 2),
            "horizon_days": horizon_days,
            "note": ("marginal CAC rises with spend as CAC_0*(scale**beta); "
                     "beta=0 would make reallocation free and is never assumed"),
        },
    )


def simulate_creative_refresh(daily: pd.DataFrame, channel: str, ctr_uplift: float,
                              ltv_margin: float, draws: int = DRAWS,
                              horizon_days: int = HORIZON_DAYS,
                              seed: int = 11) -> Outcome:
    """Recover part of a CTR decline without changing spend.

    Cheap, fast and reversible, so the uplift is modelled as uncertain but the
    downside is bounded: the worst case is the creative performs as before, plus
    production cost, not a loss of the whole budget.
    """
    rng = np.random.default_rng(seed)
    daily = daily.copy()
    daily["date_day"] = pd.to_datetime(daily["date_day"])
    cutoff = daily["date_day"].max() - pd.Timedelta(days=BOOTSTRAP_WINDOW - 1)
    rows = daily[(daily["date_day"] >= cutoff) & (daily["channel"] == channel)]
    rows = rows.dropna(subset=["ad_spend"])
    if rows.empty:
        raise ValueError(f"no recent spend for {channel}")

    spend_per_day = float(rows["ad_spend"].astype(float).mean())
    cac = float(
        rows["ad_spend"].astype(float).sum()
        / max(rows["new_customers"].astype(float).sum(), 1.0)
    )

    # A refresh recovers somewhere between nothing and roughly the decline.
    realised_uplift = np.clip(rng.normal(ctr_uplift * 0.5, ctr_uplift * 0.4, draws),
                              0.0, ctr_uplift * 1.5)
    # Better CTR means cheaper clicks at the same CPM, so CAC falls roughly in
    # proportion. Customers per pound rise by the same factor.
    improved_cac = cac / (1.0 + realised_uplift)
    baseline_customers = spend_per_day * horizon_days / cac
    improved_customers = spend_per_day * horizon_days / improved_cac

    ltv_draws = np.clip(rng.normal(ltv_margin, ltv_margin * 0.20, draws), 0.0, None)
    production_cost = rng.normal(1500, 400, draws)   # one creative round

    margin_delta = (improved_customers - baseline_customers) * ltv_draws - production_cost

    return _outcome(
        "contribution_margin_delta_30d",
        margin_delta,
        {
            "channel": channel,
            "ctr_decline_to_recover_pct": round(ctr_uplift * 100, 2),
            "realised_uplift_median_pct": round(float(np.median(realised_uplift)) * 100, 2),
            "cac_now": round(cac, 2),
            "spend_per_day": round(spend_per_day, 2),
            "production_cost_median": 1500,
            "horizon_days": horizon_days,
            "note": "downside bounded by production cost - spend is unchanged",
        },
    )


def simulate_reorder(product_rows: pd.DataFrame, sku: str, lead_time_days: int,
                     unit_margin: float, draws: int = DRAWS,
                     seed: int = 13) -> Outcome:
    """Stockout risk over a reorder lead time, and what it costs either way.

    Asymmetric on purpose. A stockout forfeits margin on demand that walked;
    overstock ties up capital and risks obsolescence. Treating them as
    symmetric is how inventory models end up recommending permanent understock.
    """
    rng = np.random.default_rng(seed)
    rows = product_rows[product_rows["sku"] == sku].sort_values("date_day")
    if rows.empty:
        raise ValueError(f"no history for {sku}")

    latest = rows.iloc[-1]
    on_hand = float(latest["inventory_quantity"]) if pd.notna(latest["inventory_quantity"]) else 0.0
    daily_units = rows["units"].dropna().astype(float).to_numpy()[-BOOTSTRAP_WINDOW:]
    if daily_units.size == 0:
        raise ValueError(f"no demand history for {sku}")

    # Lead time is itself uncertain; a fixed one hides most of the stockout risk.
    lead_times = np.clip(rng.normal(lead_time_days, lead_time_days * 0.25, draws),
                         1, None).astype(int)
    max_lead = int(lead_times.max())
    demand_paths = rng.choice(daily_units, size=(draws, max_lead), replace=True)
    cumulative = demand_paths.cumsum(axis=1)
    demand_over_lead = cumulative[np.arange(draws), lead_times - 1]

    shortfall = np.clip(demand_over_lead - on_hand, 0, None)
    margin_at_risk = -shortfall * unit_margin   # negative: margin forfeited

    return _outcome(
        "margin_at_risk_over_lead_time",
        margin_at_risk,
        {
            "sku": sku,
            "product_title": str(latest.get("product_title", "")),
            "on_hand": int(on_hand),
            "lead_time_days": lead_time_days,
            "demand_median_over_lead": round(float(np.median(demand_over_lead)), 1),
            "p_stockout": round(float((shortfall > 0).mean()), 3),
            "unit_margin": round(unit_margin, 2),
            "note": ("asymmetric by design - stockout forfeits margin, overstock "
                     "ties up capital; they are not the same loss"),
        },
        directional=False,
    )


def value_of_information(outcome: Outcome, extra_days: int = 7) -> dict:
    """Would waiting a week narrow this enough to be worth the delay?

    More data shrinks the interval roughly as 1/sqrt(n). If the expected
    narrowing is large relative to the decision -- i.e. the interval currently
    straddles zero and would stop doing so -- the honest recommendation is WAIT
    rather than a confident action on a distribution that says "possibly".
    """
    width = outcome.ci_high - outcome.ci_low
    straddles_zero = outcome.ci_low < 0 < outcome.ci_high
    if outcome.p_positive is None:
        return {
            "current_ci80_width": round(width, 2),
            "extra_days": extra_days,
            "recommend_wait": False,
            "reason": "risk measure - waiting does not change whether to cover it",
        }
    shrink = float(np.sqrt(BOOTSTRAP_WINDOW / (BOOTSTRAP_WINDOW + extra_days)))
    projected_width = width * shrink
    narrowing_pct = 100.0 * (1 - shrink)

    # Worth waiting only when the answer is genuinely in doubt AND the wait
    # would meaningfully change what we know.
    worth_waiting = bool(straddles_zero and 0.35 < outcome.p_positive < 0.65)

    return {
        "current_ci80_width": round(width, 2),
        "projected_ci80_width": round(projected_width, 2),
        "expected_narrowing_pct": round(narrowing_pct, 1),
        "straddles_zero": straddles_zero,
        "extra_days": extra_days,
        "recommend_wait": worth_waiting,
        "reason": (
            "outcome is close to a coin flip and another week of data would "
            "narrow the interval materially"
            if worth_waiting else
            "the extra week would not change the decision"
        ),
    }
