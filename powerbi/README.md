# TradingEngine — Power BI semantic model

Built over the Parquet exports of the dbt/DuckDB warehouse. Authored as TMDL so the
model diffs in git.

## Open it

1. Regenerate the data if `data/marts/` is empty:
   ```bash
   cd dbt && ../venv/Scripts/dbt.exe deps --profiles-dir . && ../venv/Scripts/dbt.exe build --profiles-dir .
   cd .. && venv/Scripts/python.exe scripts/export_marts.py
   ```
2. Open `TradingEngine.pbip` in Power BI Desktop.
3. **Set the `DataFolder` parameter** to the absolute path of `data/marts` on your machine
   (Transform data → Manage parameters). It defaults to the author's path; Power Query
   cannot resolve a path relative to the `.pbip`, so this is a one-time edit per machine.
4. Refresh.

## Model shape

Star schema. Four dimensions, five facts, two cohort tables, one measure table.

| Table | Grain | Rows | Notes |
|---|---|---:|---|
| `DimDate` | day | 365 | Marked as the date table |
| `DimChannel` | channel | 4 | Literal lookup — carries `has_cost_data` |
| `DimProduct` | variant | 24 | Carries `unit_cost`, so margin is computable |
| `DimCampaign` | campaign | 11 | 6 Meta, 5 Google |
| `FactDailyTrading` | day × channel | 1,460 | **Primary fact** |
| `FactAdSpendDaily` | campaign × day | 4,003 | CPC/CPM/CTR decomposition |
| `FactProductDaily` | day × variant | 8,760 | Velocity and cover |
| `FactEmailFlowWeekly` | flow × **week** | 318 | Weekly grain — see below |
| `FactDataQuality` | source × day | 1,095 | Completeness audit |
| `CohortRetention` | cohort × age | 132 | Not related to DimDate — see below |
| `CohortLTV` | cohort × channel × horizon | 144 | Not related to DimDate |

## Three modelling decisions worth knowing before you build visuals

**1. The cohort tables have no relationship to `DimDate`.**
`cohort_month` is month-grain and answers a different question from the daily facts.
Relating it would let someone filter to a single day and silently get one cohort's worth
of data. Use `CohortRetention[cohort_month]` or `CohortLTV[cohort_month]` as the axis.

**2. `FactEmailFlowWeekly` relates to `DimDate` via `week_start`.**
It is weekly. Filtering `DimDate` to a single day returns blank six days out of seven.
Slice it by month or by `week_start` directly.

**3. CAC is blank, never zero, where cost data does not exist.**
TikTok drives 9% of orders with no cost file. `Channel CAC` returns `BLANK()` when
`DimChannel[has_cost_data]` is false. Do not "fix" this by coalescing to zero — zero
claims free acquisition on a tenth of the business.

## Measures

28 measures in `Measures`, foldered:

| Folder | Contains |
|---|---|
| 01 Revenue & Margin | Net Revenue, Contribution Margin, Margin %, Orders, AOV |
| 02 Acquisition | New Customers, Ad Spend, Blended CAC, Channel CAC, CPC, CPM, CTR, Frequency |
| 03 Customer Value | LTV 60d (Margin), LTV 60d (Revenue), LTV / CAC |
| 04 Retention | Repeat Rate 90d, Retention Rate, Cohort Size |
| 05 Email | Open Rate, Click Rate, Email Conversion Rate, Email Revenue |
| 06 Product & Inventory | Units Sold, Velocity 28d, Days of Cover |
| 07 Data Quality | Gap Days, Complete Spend Days |

Every measure carries a description explaining what it means and where it can mislead.
Hover the field in the field list to read it.

### Two measures whose DAX is doing something non-obvious

**`Blended CAC`** excludes days with an ad-source ingestion gap from *both* numerator and
denominator. Without that filter, the two missing Meta days (2025-03-15/16) compute from
Google spend alone and read a fabricated **42% improvement** in acquisition cost.

**`Repeat Rate 90d`** filters `months_since = 1`. Cohort attributes repeat across all 11
age rows, so without that filter every cohort is counted eleven times.

## Validation status

⚠️ **The DAX in this model has been authored, not executed.** It was written offline as
TMDL with Power BI Desktop closed, and DAX cannot be verified without the engine. Until
each measure has been run against the loaded model and checked against the warehouse
figures in `docs/spec.md` §1.6, treat every number as unverified.

Reconciliation targets once loaded:

| Measure | Expected |
|---|---:|
| Net Revenue | £1,211,689.88 |
| Contribution Margin | £855,863.08 |
| Margin % | 70.6% |
| Orders | 25,720 |
| New Customers | 20,284 |
| Ad Spend | £247,493.29 |
| Gap Days | 2 |
