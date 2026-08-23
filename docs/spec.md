# Report Spec — eCommerce Trading Engine (Layer 1)

**Date:** 2026-08-23
**Status:** Awaiting client validation
**Wireframe:** [`docs/wireframe/eCommerce_Trading_Engine_Wireframe.html`](wireframe/eCommerce_Trading_Engine_Wireframe.html) — single standalone file, 1920x1080 canvas, 4 tabs
**Design doc:** [`docs/specs/2026-08-22-ecommerce-trading-engine-design.md`](specs/2026-08-22-ecommerce-trading-engine-design.md)
**Warehouse:** `dbt/` — 20 models, 95 test nodes, point-in-time correct

---

## 1. Requirements

### 1.1 Why this report exists

> "We're looking at five things: data modelling quality, signal detection, commercial reasoning, depth of thinking, communication — can you explain what you did, why, and what you'd do next **in a way a non-technical stakeholder would follow**?"
> — `data_engineer_task_brief.md`, "How we'll evaluate this"

The brief names a Jupyter notebook as the walkthrough. The examiner separately asked to see **data visualisation**, so this report is an additional deliverable, not a replacement for the notebook.

### 1.2 Audience

Two readers with different needs, and the report must serve both:

| Reader | Needs |
|---|---|
| Technical evaluator | Traceability — every number resolvable to a model and a test |
| Non-technical stakeholder | The commercial story, without SQL |

The design resolves this by pairing each finding with a plain-language "what this means" callout rather than relying on the chart alone.

### 1.3 The questions the report must answer

1. Are unit economics improving or deteriorating, and by how much?
2. Which acquisition channel is the problem, and *what specifically* about it?
3. Is the customer base retaining? (Deliberately phrased as a trap — see §4.3.)
4. Which apparent problems are not problems?

### 1.4 Scope

**In:** the six marts of Layer 1 — trading, product, cohort, LTV, email, data quality.

**Out:** Layer 2 (signal detection) and Layer 3 (recommendation + Monte Carlo). Neither exists yet, so there is no signals table and no recommendation cards. Any visual implying an automated recommendation would be misrepresenting what is built.

### 1.5 Freshness and security

- **Freshness:** static. Fixed 12-month extract, 2024-07-01 to 2025-06-30. No scheduled refresh.
- **PII:** none. Synthetic data; emails are already pseudonymous (`customer_<hash>@domain`). Safe to commit and share, including the wireframe.

### 1.6 Reconciliation source

Every figure reconciles to the warehouse's own tested totals:

| Quantity | Value | Guarded by |
|---|---:|---|
| Net revenue (ex-VAT, non-cancelled) | £1,211,689.88 | `assert_revenue_reconciles_to_source` (two exact hops) |
| Contribution margin | £855,863.08 | `assert_contribution_margin_identity` |
| Blended margin rate | 0.7063 | `assert_margin_rate_is_sane` |
| Non-cancelled orders | 25,720 | `assert_mart_money_reconciles` |
| New customers | 20,284 | `assert_new_customer_count_agrees` |
| Total ad spend | £247,493.29 | `assert_ad_spend_reconciles` |

---

## 2. EDA data notes

Profiling was done against the built warehouse, not the raw CSVs, so every figure below is what the report will actually render.

### 2.1 Grain and keys

| Mart | Grain | Rows |
|---|---|---:|
| `mart_daily_trading` | date × channel | 1,460 |
| `mart_product_daily` | date × variant | 8,760 |
| `mart_cohort_retention` | cohort_month × months_since | 132 |
| `mart_ltv` | cohort × channel × horizon | 144 |
| `mart_email_flow_weekly` | flow_name × week_start | 318 |
| `mart_data_quality` | source × date | 1,095 |

**Grain mismatch to respect in the model:** orders and ads are daily; email is weekly (53 run dates). `dim_date.week_start` is the only bridge. Do not fan-join email to a daily fact.

### 2.2 Quality issues the report must handle, not hide

| Issue | Detail | Report treatment |
|---|---|---|
| Meta ingestion gap | No rows for 2025-03-15/16 | Shown on page 4 as a finding. `blended_cac` is NULL on those days, never zero |
| Unattributed orders | 26.9% (7,139) have no usable referrer | Channel CAC labelled Tier C and confidence-discounted |
| TikTok has no cost file | 9.0% of orders (2,396), zero spend rows | CAC displayed as "not computable", never £0 |
| Blank customer emails | 623 (3.0%) | Excluded from marketable base |
| VAT | Prices VAT-inclusive; `products.cost` ex-VAT | All report figures ex-VAT; noted on any revenue visual |
| Inventory snapshot | Current stock only, no history | `days_of_cover` meaningful at latest date only |
| Partial trailing windows | First 27 days of velocity, first 7 weeks of email means | Not surfaced as trend starts |

### 2.3 Attribution constraint

`landing_site` has 7 distinct values and **zero** UTM parameters across all 26,553 orders. Last-click referrer is the only attribution available. This is why channel metrics are tiered:

| Tier | Basis | Confidence |
|---|---|---|
| A | Platform-reported (CPC, CPM, CTR, spend) | Full |
| B | Blended (blended CAC, total new customers) | Full |
| C | Channel-attributed last-click | Discounted — never drives a conclusion alone |

---

## 3. Validated metric definitions

| # | Metric | Plain-language definition | Source | Grain | Feasibility |
|---|---|---|---|---|---|
| 1 | LTV / CAC ratio | 60-day margin per acquired customer ÷ blended cost to acquire one | `mart_ltv` ÷ `mart_daily_trading` | cohort month | ✅ |
| 2 | Blended CAC | All ad spend ÷ all new customers that day | `mart_daily_trading.blended_cac` | date | ✅ Tier B |
| 3 | 60-day margin LTV | Contribution margin from a cohort's first 60 days ÷ cohort size | `mart_ltv` @ `horizon_days=60` | cohort × channel | ✅ censoring-guarded |
| 4 | Contribution margin | Ex-VAT revenue − COGS | `fct_order_line` | line | ✅ identity-tested |
| 5 | Channel CAC | Channel spend ÷ that channel's new customers | `mart_daily_trading.channel_cac` | date × channel | ⚠️ Tier C |
| 6 | Meta CPC / CPM / CTR | Cost per click, per mille, click-through rate | `fct_ad_spend_daily` | campaign × date | ✅ Tier A, identity-tested |
| 7 | Order share by channel | Last-click referrer mapping | `fct_order.channel` | order | ✅ distribution pinned by test |
| 8 | **TikTok CAC** | — | — | — | ❌ **INFEASIBLE** — no cost file. Displayed as such deliberately; the absence is the finding |
| 9 | Monthly repeat rate | Repeat orders ÷ all orders, that month | `fct_order` | date | ✅ |
| 10 | **90-day repeat rate** | % of a cohort ordering again within 90 days of their own first order | `mart_cohort_retention.repeat_rate_90d` | cohort | ✅ **added to the model for this report** — see §5 |
| 11 | Cohort retention | % of a cohort ordering again in calendar month N | `mart_cohort_retention.retention_rate` | cohort × age | ✅ guard tested both directions |
| 12 | Email open / conversion rate | Opens and orders per recipient | `mart_email_flow_weekly` | flow × week | ✅ |
| 13 | Data-quality gaps | Source-days with no rows ingested | `mart_data_quality` | source × date | ✅ |
| 14 | Days of cover | Inventory ÷ trailing 28-day velocity | `mart_product_daily` | date × variant | ⚠️ latest date only |
| 15 | Product velocity | Trailing 7d and 28d mean units/day | `mart_product_daily` | date × variant | ⚠️ partial for first 27 days |

**PROPOSED (not requested, surfaced by the data):** #10 and the LTV-horizon comparability note in §4.4. Both were added because the analysis produced them, not because the brief asked.

---

## 4. Page design and the reasoning behind it

### 4.1 Why this is not a conventional dashboard

A standard BI layout would actively **misrepresent** this dataset. Four of the findings are counter-intuitive, and the obvious visual encoding communicates the opposite of the truth:

| Conventional treatment | What it would imply | The truth |
|---|---|---|
| KPI card "Repeat Rate 24%" ▲ green | Retention healthy | It is the trap — see §4.3 |
| TikTok CAC as £0 or blank | Free acquisition | Not computable; 9% of orders |
| Two cohorts both showing 0.0% | Same outcome | One observed, one unobserved |
| "Email engagement −22%" ▼ red | Problem to fix | Measurement artifact |

The report is therefore built on **contrast pairs** — the naive reading beside the true reading — with a written callout on each. This is the single most important design decision in the spec.

### 4.2 Page 1 — Unit economics

Answers question 1. Both series on one £ axis so the convergence is literal rather than inferred.

Headline: LTV/CAC **4.42× → 2.09×**. Cost per customer +53%, value per customer −29%. Neither alone would alarm; together the ratio more than halved.

Secondary tiles separate two findings the design doc originally conflated: Vitamin D3 is the **velocity** story (3.2×, highest in catalogue, adequately stocked at 33.5d); CBD Oil 20% 30ml is the **cover** story (17.0d — the actual reorder).

### 4.3 Page 3 — the trap, and why it gets two panels

Two panels, same 0–35% scale, deliberately not one dual-axis chart:

- **Left:** blended monthly repeat rate — flat at ~24% all year.
- **Right:** 90-day repeat rate by cohort — 31.8% → 0.0%.

Both are correct. The denominators differ: the blended metric measures the whole book, the cohort metric measures each intake. The 24% is carried almost entirely by the Jul–Nov 2024 cohorts still buying; essentially no customer acquired in 2025 returns.

**Why it matters commercially:** the blended metric keeps looking healthy right up until the 2024 cohorts stop buying — a lagging indicator dressed as a current one.

The cohort matrix below uses hatching for unelapsed windows. The demonstration pair: **2024-10 at M+3 reads 0.0%** (fully observed — a real collapse) while **2025-04 at M+3 reads n/a** (window not closed; median gap to a second order is 100 days). Same number, opposite meaning.

### 4.4 Known limitation to state on the report

The LTV horizon is **not comparable across cohorts**. 14.4% of net revenue (£174,701) arrives beyond day 90, and almost all belongs to three cohorts — the July 2024 cohort earns 44.5% of its revenue after day 90; every 2025 cohort earns 0%. So a fixed-horizon LTV understates early cohorts badly and is essentially complete for recent ones, which *flatters* recent cohorts and understates how far unit economics have moved. (See README, "A finding that shapes Layer 2".)

---

## 5. Model changes made for this report

| Change | Reason |
|---|---|
| `mart_cohort_retention.repeat_within_90d`, `repeat_rate_90d`, `has_full_90d_exposure` | The headline 31.8% → 0.0% figure existed in no mart. Defined once in the warehouse where tests guard it, rather than redefined in DAX where nothing does |
| `assert_censored_90d_is_null`, `assert_exposed_90d_is_populated` | Both directions, mirroring the existing guard pair |
| `scripts/export_marts.py` → `data/marts/*.parquet` | Power BI reads Parquet, not the gitignored `.duckdb` file. Also the handoff format for Layers 2–3 |

---

## 6. Open items

- `OPEN:` No business rules or thresholds supplied (what CAC/LTV ratio constitutes "unhealthy"? what days-of-cover triggers a reorder?). The report presents measured values without a RAG status. Thresholds would need client input before any conditional formatting is added.
- `OPEN:` No target or budget figures exist in the data, so no variance-to-plan visuals are possible.
- `(inferred)` Peak season is taken as November–December from the observed order distribution; not stated anywhere in the brief.
- `(inferred)` The 60-day LTV horizon is taken from the brief's own worked example ("Meta CAC has exceeded the 60-day LTV threshold"), not from a client rule.

---

## 7. Confidence

**Awaiting validation.**

Metrics 1–15 are feasible and reconciled against the warehouse. Metric 8 is confirmed infeasible and displayed as such by design. The wireframe is built from real figures throughout — nothing is illustrative except the layout.
