# eCommerce Trading Engine — Design Spec

**Date:** 2026-08-22
**Status:** Approved for planning

---

## 1. Problem

Build the foundation of an "eCommerce Trading Engine": a system that models 12 months of a D2C brand's operational data, detects meaningful signals, maps them to commercial recommendations, and quantifies expected outcomes under uncertainty.

The brief defines three layers. All three are in scope; layer 3 is built for real but covers a focused set of recommendation types rather than every detected signal.

## 2. Dataset findings that drive the design

These were established by profiling before any code was written. They are the reason the architecture looks the way it does.

### 2.1 Shape

- Clean 12-month window: 2024-07-01 to 2025-06-30.
- UK / GBP only. `source_name` is `web` for all 26,553 orders — single sales channel.
- Referential integrity is perfect: zero orphan order lines, zero unknown customers, zero unknown variants. Every planted problem is behavioural, not structural.
- Grain differs by source: orders and ads are daily; email flows are weekly (53 run dates).
- Prices are VAT-inclusive. `taxes_included` is true on every order, `total_price = subtotal_price + shipping` exactly, and `total_tax = subtotal_price / 6` exactly on all 25,720 non-cancelled orders — 20% UK VAT already inside the listed price. `products.cost` is an ex-VAT cost price, so revenue must be divided by 1.2 before margin is taken. See section 4.2.
- Line-level totals reconcile to order-level totals exactly: summed `price × quantity` equals `total_line_items_price` and summed `total_discount` equals `total_discounts` for all 26,553 orders, with zero variance.

### 2.2 Genuine commercial events

| Event | Evidence |
|---|---|
| Meta cost inflation | CPC £0.42 → £1.02 (+143%) from 2025-04, all 6 campaigns simultaneously. Decomposes into CPM £10.00 → £15.50 (+55%, auction pressure) and CTR 2.36% → 1.52% (−36%, creative relevance). Frequency flat ~1.22 all year, so not audience saturation. Google CPC flat at ~£0.19 all year. |
| Vitamin D3 breakout | 219 → 701 units/month (3.2×), sustained from 2025-02, while all 11 other products stayed flat. Combined inventory 812 units vs ~620/month run rate ≈ 5.7 weeks cover. Stockout risk. |
| AOV erosion | £61.36 → £55.07 (−10%). Discount rate is flat at ~3.2%, so this is mix shift (D3 sells at £12.99–16.99 vs CBD Oil at £29.99–109.99), not promotional dependency. |
| Blended CAC rise | £9.68 → £14.84 (+53%). Attribution-free. |
| Cohort retention collapse | 90-day repeat rate falls monotonically across cohorts that ALL have full 90-day exposure: 31.8% (2024-07) → 15.8% (2024-09) → 2.4% (2024-11) → 0.2% (2025-01) → 0.0% (2025-03). Not a censoring artifact. Verified against the CSVs. |
| Unit-economics compression | 60-day contribution-margin LTV £42.80 → £30.24 (−29%) while blended CAC rose 53%. LTV/CAC **4.4× → 2.1×** — more than halved in twelve months. This is the headline commercial finding. |

### 2.3 Traps — things that look like signals but are not

| Trap | Why it is not a commercial event |
|---|---|
| "Stable repeat rate" | The monthly repeat-order rate is genuinely stable at ~24% all year — and it is deeply misleading. It is carried almost entirely by the Jul–Nov 2024 cohorts continuing to buy. Essentially no customer acquired in 2025 ever returns. The blended metric looks healthy while the cohort quality underneath it collapsed. This is the single largest trap in the dataset, and it points the opposite way to the obvious reading. |
| Censoring on recent cohorts | Right-censoring IS real, but it explains only the most recent two to three cohorts (2025-04 onward at 90 days). Median gap to a 2nd order is 100 days (p75 = 195). Cohorts through 2025-03 have full 90-day exposure, so their near-zero retention is NOT a censoring artifact. A censoring guard is still required, or recent cohorts get reported as final when they are only partly observed. |
| Email engagement decay | Open and click rates fall across all six flows from ~2025-03 (Welcome open 52% → 40%), but conversion rates are flat (Welcome 3.37% → 3.23%, Cart 4.05% → 3.86%). Engagement degradation with intact monetisation indicates a deliverability or measurement change, not lost demand. |
| Meta "spend crash" | 2025-03-15 and 2025-03-16 are simply missing rows in `meta_ads_daily.csv` (363 dates vs Google's 365). A naive daily detector reports spend going to zero. |
| Email identity | `orders.email` differs from `customers.email` for 22,810 of 26,553 orders (86%) — same local-part, different domain. Joining or deduplicating on email corrupts every customer-level metric. Must join on `customer_id`. |
| Google conversion gap | Platform-reported conversions are a stable ~2.0× the Shopify-attributed order count all year. A consistent ratio is an attribution methodology difference, not a fault. |
| Blank emails | 623 customers (3.0%) have an empty email string, overstating the marketable base. |
| `order_count` drift | 825 customers' `order_count` disagrees with derived counts, because the source field includes cancelled orders while `total_spent` excludes them. Definitional, not corrupt. |
| Seasonality | November/December peak, January trough. Any non-seasonal baseline flags January as a crisis. |

### 2.4 Attribution constraint (validated)

There is no richer attribution available than last-click referrer:

- `landing_site` has only 7 distinct values and **zero** query strings or UTM parameters across all 26,553 orders.
- `referring_site` has 7 values, mapped as: facebook + instagram → Meta, google + youtube → Google, tiktok → TikTok, blank + `direct` → Unattributed.

| Channel | Orders | Share | Cost data |
|---|---:|---:|---|
| Google | 9,705 | 36.5% | yes |
| Meta | 7,313 | 27.5% | yes |
| Unattributed | 7,139 | 26.9% | n/a |
| TikTok | 2,396 | 9.0% | none supplied |

Two consequences:

1. **TikTok CAC is structurally uncomputable** — 9% of orders have no cost file. Blended CAC is therefore the only complete cost measure.
2. **Meta's channel-attributed orders do not track its clicks.** Between 2024-07 and 2025-06 Meta clicks fell 48% (24,803 → 12,809) while Meta-attributed orders fell only 9% (551 → 502), pushing orders-per-click from a stable 2.2% to 3.92%. Google's orders-per-click is steady at ~1.7% (corr(clicks, orders) = 0.983); Meta's correlation is 0.842 and breaks from 2025-05. Meta's *share* of all orders is flat all year (27.6% → 26.6%).

Therefore the revenue-side evidence for Meta is weak, while the **cost-side evidence is certain** because CPM, CTR and frequency are platform-reported and require no attribution.

## 3. Architecture

```
CSVs --dbt--> staging --> core (star schema) --> metric spine --+
                                                                |
        +-------------------------------------------------------+
        v
   detectors --> signals --> classifier --> recommendations --> simulation --> decision
                             (COMMERCIAL /                      (Monte Carlo)  (autonomy gate)
                              DATA_QUALITY /
                              ARTIFACT)
```

**Chosen split: dbt builds a metric spine, Python detects on top of it.**

Rejected alternatives:

- *All detection in dbt SQL*: seasonal decomposition, robust z-scores, FDR control and Monte Carlo are impractical or impossible in SQL.
- *All in Python with dbt as a loader*: discards the data-modelling quality that is the first evaluation criterion.

Every stage is parameterised by `as_of_date`, threaded from the CLI into a dbt var.

### 3.1 Point-in-time correctness

Each source carries `_weld_synced`, the ingestion timestamp, distinct from event time (ad rows sync at next-day 00:00). Row availability is `COALESCE(_weld_synced, event_time) <= as_of_date`, while event time drives metric attribution. This makes the backtest honest about reporting lag instead of pretending data appears instantly.

`customers._weld_synced` is blank throughout, hence the COALESCE fallback.

## 4. Layer 1 — data model (dbt + DuckDB)

### 4.1 Staging

One model per source. Staging may cast, rename, normalise a single column, and derive a
column from **one** source column deterministically (`channel` from `referring_site`,
`has_valid_email` from `email`, `margin_pct` from `price` and `cost`). Staging may NOT
join across sources, aggregate, or encode a rule that spans more than one row.

This wording is deliberate and was tightened after review. The original phrasing was
"casting and renaming only; no business logic", which the staging models genuinely violate:
a LIKE-based channel taxonomy and a VAT-adjusted margin ratio are neither casts nor renames.
Rather than pretend otherwise or scatter those derivations across every downstream consumer,
the constraint now states the real intent — the boundary that matters is joins and
aggregation, not arithmetic.

- `stg_orders` — cast timestamps and money, derive `is_cancelled`, map `referring_site` to `channel`.
- `stg_order_lines`, `stg_customers` (flag blank email), `stg_products` (derive margin).
- `stg_ads_daily` — unions Meta and Google onto a common grain (`platform`, `campaign_id`, `date`). Google's `cost_micros` is divided by 1e6. **Meta's conversions are NULL, not 0** — the absence of Meta conversion reporting is a modelled fact, and coercing it to zero would silently corrupt every ROAS calculation.
- `stg_email_flows` — PascalCase to snake_case, `Run_Date` to `week_start`.

### 4.2 Core (star schema)

| Model | Grain | Notes |
|---|---|---|
| `dim_customer` | customer_id | first_order_date, acquisition_channel, consent state, `has_valid_email` |
| `dim_product` | variant_id | price, `cost`, `margin_pct`, inventory |
| `dim_date` | date | full spine, `is_weekend`, `iso_week`, `is_peak_season` |
| `dim_campaign` | platform + campaign_id | `funnel_stage` parsed from campaign name (Prospecting / Retargeting / DPA / Brand / Non-Brand / PMax / Shopping) |
| `fct_order` | order_id | revenue, discount, tax, shipping, is_cancelled, channel |
| `fct_order_line` | order_line_id | quantity, gross, discount, net, COGS, contribution_margin |
| `fct_ad_spend_daily` | platform × campaign × date | spend, impressions, clicks, platform_conversions (nullable) |
| `fct_email_flow_weekly` | flow × message × week | recipients, opens, clicks, unsubs, orders, revenue |

`fct_order_line` carries COGS and contribution margin so that **every downstream metric is margin-based, not revenue-based**. Ex-VAT product margins range 64.0% to 82.0%, so revenue-based and margin-based recommendations diverge materially.

**VAT handling is a correctness requirement, not a refinement.** Order and line values are VAT-inclusive while `products.cost` is ex-VAT, so `net_revenue` divides the discounted line value by `1 + vat_rate` before COGS is subtracted. Taking margin on the VAT-inclusive figure overstates it by roughly 6 to 8 percentage points per variant (CBD Oil 10ml reads 71.7% instead of its true 66.0%) and would inflate every LTV, ROAS and simulated margin impact in the engine. `vat_rate` is a dbt var defaulting to `0.20`.

The daily/weekly grain mismatch is explicit: `dim_date.iso_week` is the only bridge, and no model fan-joins email to orders.

### 4.3 Marts — the metric spine

The only surface detectors read.

| Mart | Grain | Purpose |
|---|---|---|
| `mart_daily_trading` | date × channel | orders, contribution margin, new/returning customers, AOV, spend, CAC, ROAS, blended CAC |
| `mart_product_daily` | date × variant | units, trailing 7d/28d velocity, days-of-cover |
| `mart_cohort_retention` | cohort × age | carries `cohort_size` and an exposure-sufficiency flag (`has_full_exposure`) — the censoring fix |
| `mart_ltv` | cohort × channel × horizon | contribution-margin LTV at 30/60/90 days, censoring-aware |
| `mart_email_flow_weekly` | flow × week | open/click/conversion rate, revenue per recipient |
| `mart_data_quality` | source × date | coverage gaps, null rates, derived-vs-precomputed drift |

`mart_data_quality` is a first-class table rather than notebook commentary. This is what makes "distinguish a data quality issue from a commercial event" a mechanical join instead of an assertion.

### 4.4 Tests

Standard dbt tests (unique, not_null, relationships, accepted_values) on all keys, plus singular tests:

- **Date-spine completeness per source** — this is what catches Meta's missing 2025-03-15/16. It is expected to fail on first run; that failure is the deliverable.
- Contribution margin never exceeds net revenue.
- Ad spend and quantities are non-negative.
- Reconciliation: mart revenue totals equal raw CSV totals.

## 5. Layer 2 — signal detection

### 5.1 Signal interface

```python
Signal(
    signal_id, detector, entity_type, entity_id,
    as_of_date, fired_date,
    severity,            # 0-1
    direction,           # improving | degrading
    evidence,            # dict of the supporting numbers
    classification,      # COMMERCIAL | DATA_QUALITY | ARTIFACT
    attribution_tier,    # A | B | C
    data_quality_score,  # 0-1
)
```

Thresholds live in `config/detectors.yml`, not in code.

### 5.2 Detectors

1. `cac_ltv_breach` — channel CAC vs 60-day contribution LTV, N consecutive days.
2. `cac_trend` — Theil–Sen slope plus Mann–Kendall test on trailing-28d CAC.
3. `cpc_decomposition` — splits CPC movement into CPM and CTR components.
4. `product_velocity` — trailing 7d vs lagged 28d, MAD-based robust z-score, seasonally adjusted.
5. `inventory_cover` — days of cover vs velocity, stockout risk.
6. `aov_decomposition` — separates mix, price and discount effects.
7. `email_engagement_decay` — engagement trend with a conversion co-movement check.
8. `cohort_retention_shift` — compares cohorts only at equal exposure age with sufficient `cohort_size` and `has_full_exposure`. Fires COMMERCIAL on the fully-exposed decline (31.8% → 0.0% at 90 days) and stays silent only on genuinely censored cohorts.
9. `channel_cvr_shift` — orders per click by channel (no session data available).
10. `data_completeness` — missing dates per source.
11. `attribution_divergence` — platform conversions vs Shopify-attributed; fires only when the *ratio* breaks trend.

### 5.3 Separating signal from noise

Four mechanisms, all required:

1. **Seasonal baselines** — STL decomposition or a robust day-of-week × month-of-year baseline, so the January trough does not fire.
2. **Robust statistics** — MAD-based z-scores and Theil–Sen slopes, not mean and standard deviation, which the November/December peak would distort.
3. **Persistence** — N consecutive periods above threshold, eliminating single-period noise.
4. **FDR control** — Benjamini–Hochberg across roughly 11 detectors × 20 entities × 365 days. At that scale false positives are manufactured by construction, and controlling the false discovery rate is the concrete answer to "can you distinguish signal from noise".

### 5.4 Three-way classification

- **DATA_QUALITY** — `mart_data_quality` flags an incident overlapping the signal window. Catches the Meta missing days.
- **ARTIFACT** — explained by a known structural cause: insufficient cohort exposure (censoring), the seasonal component, or a stable attribution ratio. Catches the 2.0× Google conversion gap and the near-zero retention of the two most recent cohorts. It must NOT suppress the genuine retention collapse in fully-exposed cohorts — the guard exists to make that distinction, not to explain the whole decline away.
- **COMMERCIAL** — survives both, persists, and clears FDR.

**The discriminating rule:** does the downstream commercial metric co-move with the engagement metric?

- Email: opens −23%, conversion rate flat → ARTIFACT.
- Meta: CTR −36% **and cost per click +143%** → COMMERCIAL, on cost-side evidence.

### 5.5 Attribution tiering

Every signal is tagged with the attribution dependence of its evidence:

| Tier | Basis | Examples | Confidence treatment |
|---|---|---|---|
| A | Platform-reported, attribution-free | CPC, CPM, CTR, frequency, spend, units, margin | Full |
| B | Blended, no attribution needed | blended CAC, total new customers, AOV | Full |
| C | Channel-attributed last-click | channel CAC, channel LTV, channel ROAS | Discounted |

Confidence scores multiply by an attribution-reliability factor. **A Tier C signal can never drive an autonomous action on its own.** Given 26.9% of orders are unattributed and 9% have no cost data at all, this is a correctness requirement, not conservatism.

## 6. Layer 3 — recommendation, simulation, decision

### 6.1 Recommendations

Signal-to-action mapping in `config/actions.yml`. Action types: `REALLOCATE_SPEND`, `REFRESH_CREATIVE`, `REORDER_INVENTORY`, `PAUSE_CAMPAIGN`, `ADJUST_FLOW`, `INVESTIGATE_DATA`.

Each recommendation carries action type, magnitude, generated rationale, confidence, reversibility class, and a simulated outcome distribution.

**The Meta recommendation is two-pronged**, because the CPC decomposition shows roughly half the damage is auction inflation (uncontrollable) and half is creative relevance (controllable):

1. `REFRESH_CREATIVE` — addresses the CTR half. Cheap, fast, reversible.
2. `REALLOCATE_SPEND` — a **bounded 20% test shift** to Google, sized to the auction-attributable portion only.

The reallocation is deliberately bounded rather than aggressive: the cost signal is Tier A and certain, but the incremental-revenue signal is Tier C and unreliable. Sizing a reversible test to the level of confidence in the evidence is the core trading-engine behaviour.

### 6.2 Simulation

Monte Carlo, ~10,000 draws, inputs bootstrapped from trailing-90d variance in the metric spine.

**Google marginal CAC must be modelled as rising with spend**: `CAC_0 * (spend/spend_0)^beta`, with `beta` sampled from a prior informed by the observed spend–CAC relationship. Assuming linear scaling would make every reallocation appear free and is the most common way this kind of model produces nonsense.

Outputs per recommendation: distribution of 30-day contribution-margin delta, P(ROI > 0), and an 80% credible interval. Never a point estimate.

For inventory: demand forecast distribution from bootstrapped velocity, lead-time uncertainty, and asymmetric costs — lost margin on stockout versus tied-up capital and obsolescence on overstock.

### 6.3 Autonomy gate

| Confidence | Reversible (ad spend) | Irreversible (inventory purchase) |
|---|---|---|
| > 0.8 | AUTO-EXECUTE | FLAG FOR REVIEW |
| 0.5 – 0.8 | Auto, capped magnitude | FLAG FOR REVIEW |
| < 0.5 | MONITOR | MONITOR |

Plus a **value-of-information** check: if waiting 7 more days materially narrows the outcome distribution, the recommendation becomes `WAIT`. Reversibility governs the *ceiling* on autonomy; confidence governs the *magnitude*.

## 7. Backtest

Replay `as_of_date` across all 365 days. For each detector record first-fire date, persistence, and precision / recall / lead time against hand-labelled ground truth in `config/ground_truth.yml`.

**Stated limitation:** the ground-truth labels are the author's own reading of the planted signals, derived from the profiling in section 2. The backtest therefore measures internal consistency, not external validity. This is documented prominently rather than presented as independent validation.

## 8. Deliverable structure

```
de_task/
  README.md                  approach, assumptions, what I would build next
  data/                      provided CSVs
  dbt/
    dbt_project.yml, profiles.yml
    models/{staging,core,marts}/
    macros/, tests/
  engine/
    cli.py, io.py, signals.py
    detectors/
    classify.py, recommend.py, simulate.py, decide.py, backtest.py
  config/
    detectors.yml, actions.yml, economics.yml, ground_truth.yml
  notebooks/walkthrough.ipynb
  tests/                     pytest
  docs/specs/
```

CLI surface:

```
python -m engine run      --as-of 2025-06-30
python -m engine backtest
```

## 9. Environment

- Python 3.12.4 (venv recreated from 3.14.2, which dbt-core does not support).
- dbt-core 1.12.3, dbt-duckdb 1.11.0, duckdb 1.5.5, pandas 3.0.5, numpy 2.5.2, scipy 1.18.1, scikit-learn 1.9.0, jupyter.
- Git repository initialised at `de_task/`. The enclosing `C:/Users/nguye` git repository is accidental and is left untouched.

## 10. Build sequence and verification

| # | Step | Verification |
|---|---|---|
| 1 | git init, dbt scaffold | `dbt debug` and `dbt build` run clean |
| 2 | staging + core + tests | Date-completeness test fails on Meta, proving the test works |
| 3 | Metric spine | Mart revenue reconciles to raw CSV totals |
| 4 | Detectors + classifier | Retention detector fires COMMERCIAL on fully-exposed cohorts and silent only on censored ones; email classifies ARTIFACT; Meta classifies COMMERCIAL |
| 5 | Recommendations + Monte Carlo + autonomy gate | CLI prints the recommendation brief |
| 6 | Backtest | Precision / recall / lead-time table produced |
| 7 | Notebook + README | Walkthrough runs top to bottom |

## 11. Assumptions

1. Last-click referrer attribution is the only option available and is used as the primary channel basis, with blended CAC as the attribution-free cross-check.
2. LTV is contribution-margin based, not revenue based, and computed on ex-VAT revenue at a 20% rate (`vat_rate` var). All headline revenue and AOV figures quoted in section 2 are VAT-inclusive, matching the source; the warehouse reports ex-VAT.
3. Cancelled orders (833, all also refunded) are excluded from revenue and retention; `customers.order_count` includes them, which explains the 825-row drift.
4. Customers are joined on `customer_id` only. Email is never a join or dedup key.
5. The 60-day LTV horizon from the brief's worked example is the default CAC comparison window; configurable in `config/economics.yml`.
6. Inventory lead times and holding costs are not supplied; assumed values live in `config/economics.yml` and are flagged as inputs a client would confirm.

## 12. Out of scope

- Sessions or traffic data (not supplied), so conversion rate is proxied by orders per click.
- TikTok cost data (not supplied), so TikTok CAC is not computed.
- Multi-touch or time-decay attribution (no UTM or touchpoint data exists to support it).
- Real-time or streaming ingestion; the engine is batch, parameterised by `as_of_date`.
