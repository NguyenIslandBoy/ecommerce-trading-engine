# eCommerce Trading Engine

A signal detection and recommendation system built on 12 months of a D2C
brand's operational data.

## Status

| Layer | State |
|---|---|
| 1. Data foundation | Built |
| 2. Signal detection | Planned |
| 3. Recommendation and simulation | Planned |

## Quickstart

```bash
# Python 3.12 required — dbt-core does not support 3.14
uv venv --python 3.12 venv
uv pip install --python venv/Scripts/python.exe -r requirements.txt

cd dbt
../venv/Scripts/dbt.exe deps --profiles-dir .
../venv/Scripts/dbt.exe build --profiles-dir .
```

If `uv` isn't available, `python -m venv venv && venv/Scripts/python -m pip install -r requirements.txt` also works.

Rebuild the warehouse as of any historical date:

```bash
../venv/Scripts/dbt.exe build --profiles-dir . --vars '{as_of_date: 2025-01-31}'
```

## Data model

Three layers: `staging` (may cast, rename, normalise and derive
deterministically from a single source column, but may not join across
sources or aggregate), `core` (star schema with COGS and contribution
margin), `marts` (the metric spine that the detection layer reads).

See `docs/specs/2026-08-22-ecommerce-trading-engine-design.md` for the
full design and the profiling that motivated it.

## Deliberate design decisions

**Point-in-time correctness.** Every staging model that can filters on row
*availability* (`_weld_synced`, the ingestion timestamp) rather than event
time — `stg_products` cannot (`products.csv` carries no `_weld_synced`
column, only a current snapshot) and `stg_order_lines` has no such column
either, so it inherits availability via its join to `stg_orders`. The
whole warehouse can be rebuilt as of any date, which is what
makes backtesting the detection layer honest. `as_of_date` defaults to
`2025-07-01` — the day *after* the 12-month period closes on
2025-06-30 — because ad and email sources carry a 1-day ingestion lag
(a row for event date 2025-06-30 has `_weld_synced` of 2025-07-01) while
orders sync same-day. Running on the close date itself would leave the
final day's orders with NULL spend and NULL CAC; running the day after is
both the natural operating pattern and the first moment the complete
12 months is actually visible. Point-in-time correctness is preserved at
any earlier cursor too — a rebuild at `as_of_date: 2025-01-31` produces
`mart_daily_trading` with `max(date_day) = 2025-01-31` and exactly
860 rows (215 days × 4 channels), proving the whole warehouse — not just
the ad tables — respects the cursor.

**Margin, not revenue.** `fct_order_line` carries COGS and contribution
margin. Ex-VAT catalogue margins range 64.0% to 82.0%, so revenue-based
conclusions diverge materially from margin-based ones.

**VAT is removed before margin is taken.** Source prices are
VAT-inclusive (`taxes_included` is true on every order and
`total_tax = subtotal_price / 6` exactly), while `products.cost` is an
ex-VAT cost price. Subtracting one from the other directly would
overstate margin by 6 to 8 points per variant and inflate every LTV and
ROAS in the engine. `net_revenue` is ex-VAT; `net_revenue_incl_vat` is
retained purely to reconcile against source order totals.

**Meta conversions are NULL, not zero.** Meta reports no conversion data
at all. Coercing that to zero would make Meta's ROAS read as 0.0 rather
than "unknown".

**Customers join on `customer_id` only.** `orders.email` differs from
`customers.email` for 86% of orders — same local part, different domain.
`assert_no_email_join_used` is a standing guardrail: it fails the build
if an email-based join would ever match more than 20% of orders.

## Known data quality issues

| Issue                 | Detail                                                      | Handling                                                                                                                                                    |
| --------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Missing ad days       | `meta_ads_daily` has no rows for 2025-03-15 or 2025-03-16 | Detected by `assert_source_date_completeness`, which warns on every build. Surfaced in `mart_data_quality` so detectors can reclassify affected signals. |
| Blank emails          | 623 customers (3.0%) have an empty email                    | `has_valid_email` and `is_marketable` on `dim_customer`                                                                                               |
| `order_count` drift | 825 customers disagree with derived counts                  | Source field counts cancelled orders; `total_spent` does not. Documented, not "fixed".                                                                     |
| Unattributed orders   | 26.9% have no usable referrer                               | Channel metrics are confidence-discounted downstream                                                                                                        |
| No TikTok cost data   | 9.0% of orders, no spend file                               | TikTok CAC is NULL by construction; blended CAC (total spend / total new customers, attribution-free) is the only complete cost measure                     |
| Inventory is a snapshot | `products.csv` carries only CURRENT `inventory_quantity`, with no history | `mart_product_daily.days_of_cover` applies that snapshot to every historical day, so it is meaningful only at the latest date. Any backtest must read inventory signals at the run date, never historically. |
| Cost is a snapshot | `products.csv` carries only a single CURRENT `cost` per variant, with no history | `fct_order_line` applies that present-day cost to every historical order (COGS = current `unit_cost` × quantity), so margin trends are affected by any cost change that actually occurred during the period, and the engine cannot see it or correct for it. |
| Partial trailing windows at series start | Trailing-window columns average over fewer real days than their label implies until the window fills | `mart_product_daily.velocity_28d` and `days_of_cover` are unreliable for the first 27 days of the spine (2024-07-01 reads `velocity_28d` = 7.0 from a single day of data, `days_of_cover` = 67.4); `mart_email_flow_weekly`'s 8-week trailing means are likewise unreliable for the first 7 weeks. Not fixed here; a detector consuming these columns must discard the warm-up period. |
| Every rebuild's newest day has NULL ad spend | Ad and email sources sync one day after event date, but `dim_date` clamps to `max(order_date)`, not to the ad/email max — so at ANY `as_of_date` cursor D, ad spend is complete only through D−1 | The newest day of every historical rebuild has NULL `ad_spend`, `channel_cac` and `blended_cac` in `mart_daily_trading`, by design (point-in-time correctness, not a bug). A Layer 3 backtest replaying `as_of_date` across ~365 cursors will hit this on every single run; the detection layer must expect and discard that trailing day rather than treat it as a data gap. |

## The retention trap

Cohort retention really does collapse: the 90-day repeat rate (the share
of a cohort placing a second order within 90 days of their first) falls
from 31.8% (2024-07 cohort) to 0.0% (2025-03 cohort) across cohorts that
all have full 90-day exposure — near-monotonically: 2025-01 and 2025-02
sit level at ~0.2% before reaching zero (31.77, 25.17, 15.79, 9.65, 2.41,
0.21, 0.17, 0.23, 0.00) — and every one of those cohorts has full 90-day
exposure — the observation
window has fully elapsed against the last date with data (2025-06-30).
Censoring explains only the most recent two to three cohorts (2025-04
onward), where the median 100-day gap to a second order means the window
has not closed yet.

`mart_cohort_retention` carries `has_full_exposure` and returns NULL for
`retention_rate` wherever the observation window has not elapsed.
`raw_retention_rate` is kept alongside it to show the difference.

Note the 31.8% → 0.0% figure above is a *rolling* 90-day repeat rate
(second order within 90 days of the first, computed directly from order
dates), which is a different number from `mart_cohort_retention.retention_rate`,
which buckets by discrete calendar month instead — the same 2024-07
cohort reads 13.89% there at `months_since = 3`. Both are correct
measurements; they just answer slightly different questions, and both
show the same collapse.

**The rolling 90-day figure is not a column in any mart.** It is quoted
here as the headline finding, but no model in this build computes a
rolling (order-to-order) repeat-rate window — only the calendar-month
bucketed `mart_cohort_retention.retention_rate`. Layer 2 (detection) will
need to add a rolling-window measure if it wants to reproduce this exact
number mechanically rather than by ad hoc query.

The trap runs the other way from the obvious reading: the monthly
repeat-order rate IS stable at roughly 24% all year, but that stability
is carried by the Jul-Nov 2024 cohorts continuing to buy. Essentially no
customer acquired in 2025 returns. The blended metric looks healthy while
the cohort quality underneath it collapsed.

## What I would build next

Layer 2 (detection) and Layer 3 (recommendation and simulation) — see the
design spec.
