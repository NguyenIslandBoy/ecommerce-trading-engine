# Report Build Guide — drag-and-drop mapping

Maps every visual in [the wireframe](wireframe/eCommerce_Trading_Engine_Wireframe.html) to the
exact fields and measures to drop into each field well.

**Model:** `powerbi/TradingEngine.pbip` — open it, set the `DataFolder` parameter, refresh.

⚠️ **Measures are authored but not yet execution-validated.** Reconcile against `docs/spec.md`
§1.6 before trusting any figure. See "Validate first" at the end.

---

## Page 1 — Unit economics

### KPI cards (4 across the top)

| Card | Field well | Field |
|---|---|---|
| LTV / CAC ratio | Fields | `[LTV / CAC]` |
| Blended CAC | Fields | `[Blended CAC]` |
| 60-day margin LTV | Fields | `[LTV 60d (Margin)]` |
| Contribution margin | Fields | `[Contribution Margin]` |

Use a **Card (new)** visual for each. For the "from X · −53%" subtitle, add a second card or a
text box — the delta is against the first cohort, not a time-intelligence calculation, so there
is no built-in comparison to bind.

### Main chart — value vs cost

**Line chart**

| Well | Field |
|---|---|
| X-axis | `CohortLTV[cohort_month]` |
| Y-axis | `[LTV 60d (Margin)]` |
| Y-axis | `[Blended CAC]` |

Both are £ on the same scale, so use a single Y-axis, not a dual axis — the point is that the
lines converge, and a dual axis would let you fake or hide that.

> **Watch:** the last two cohorts (2025-05, 2025-06) are censored. `[LTV 60d (Margin)]` returns
> blank for them by design. If you see a value there, the exposure guard is not working.

### Tiles

| Tile | Visual | Fields |
|---|---|---|
| Product velocity | Card + text | `[Units Sold]` filtered to `DimProduct[product_title] = "Vitamin D3 Drops"` |
| Inventory cover | Table | `DimProduct[sku]`, `[Days of Cover]`, sorted ascending, Top 3 |
| Data completeness | Card | `[Gap Days]` |

The cover tile must sort **ascending** and show the worst-covered SKU. Sorting descending, or
showing an average, hides the binding constraint — the reorder is driven by the worst variant,
not the mean.

---

## Page 2 — Acquisition cost

### KPI cards

| Card | Field |
|---|---|
| Meta CAC | `[Channel CAC]`, filtered `DimChannel[channel] = "meta"` |
| Google CAC | `[Channel CAC]`, filtered `DimChannel[channel] = "google"` |
| Meta CPC | `[CPC]`, filtered `FactAdSpendDaily[platform] = "meta"` |
| TikTok CAC | `[Channel CAC]`, filtered `DimChannel[channel] = "tiktok"` |

The TikTok card **will render blank**. That is correct and is the finding. Set the card's "Show
blank as" to a dash or leave it — do not set it to 0.

### CPC decomposition chart

**Line chart**, filtered to `FactAdSpendDaily[platform] = "meta"`

| Well | Field |
|---|---|
| X-axis | `DimDate[year_month]` |
| Y-axis | `[CPC]`, `[CPM]`, `[CTR]` |

These have different units, so index them. Add three measures alongside the existing ones:

```dax
CPC Indexed =
VAR Base = CALCULATE([CPC], ALLSELECTED(DimDate), FactAdSpendDaily[platform] = "meta")
RETURN DIVIDE([CPC], Base) * 100
```

…and the same shape for CPM and CTR. Indexing to the first period is what makes the two
components visually comparable and shows they turn together in April 2025.

### Channel share tiles

**Table** or four cards

| Well | Field |
|---|---|
| Rows | `DimChannel[channel_name]` |
| Values | `[Orders]`, `[Channel CAC]` |
| Sort | `DimChannel[sort_order]` |

Set `DimChannel[channel_name]` to sort by `DimChannel[sort_order]` (Column tools → Sort by
column), or the axis comes out alphabetical.

### Blocked visual

The hatched "TikTok CAC — deliberately not shown" card is a **text box**, not a data visual.
It exists to explain an absence, and a blank chart would read as a bug rather than a finding.

---

## Page 3 — Customer value

### Left panel — the blended metric

**Line chart**

| Well | Field |
|---|---|
| X-axis | `DimDate[year_month]` |
| Y-axis | a `Repeat Order Share` measure (see below) |

This one is not yet in the model — it is a whole-book metric rather than a cohort one:

```dax
Repeat Order Share =
DIVIDE(
    SUM(FactDailyTrading[returning_customers]),
    SUM(FactDailyTrading[orders])
)
```

### Right panel — the cohort metric

**Line chart**

| Well | Field |
|---|---|
| X-axis | `CohortRetention[cohort_month]` |
| Y-axis | `[Repeat Rate 90d]` |

**Set both charts' Y-axis to a fixed 0–35% range.** Auto-scaling defeats the entire page: the
comparison only works if the two panels share a scale.

### Cohort matrix

**Matrix**

| Well | Field |
|---|---|
| Rows | `CohortRetention[cohort_month]` |
| Columns | `CohortRetention[months_since]` |
| Values | `[Retention Rate]` |

Conditional formatting on `[Retention Rate]`: a colour scale, and **blank cells left blank**.
A censored cell returns blank deliberately — formatting blank as zero would erase the
distinction the whole model is built to make.

> **The demonstration pair:** 2024-10 at M+3 shows `0.0%` (observed) while 2025-04 at M+3 shows
> blank (unobserved). Same underlying count, opposite meaning. If both render as 0.0%, the guard
> is broken.

---

## Page 4 — Signal vs artifact

### Email engagement vs conversion

**Line chart**, filtered to `FactEmailFlowWeekly[flow_name] = "Welcome Series"`

| Well | Field |
|---|---|
| X-axis | `DimDate[year_month]` |
| Y-axis | `[Open Rate]`, `[Email Conversion Rate]` |

Index both to the first period as on page 2 — open rate is ~52% and conversion ~3.3%, so
un-indexed the conversion line is a flat sliver at the bottom and the contrast is invisible.

### Data quality

**Table**

| Well | Field |
|---|---|
| Rows | `FactDataQuality[source_name]`, `FactDataQuality[date_day]` |
| Values | `FactDataQuality[issue_type]` |
| Filter | `FactDataQuality[is_gap] = True` |

Two rows. Both `meta_ads_daily`.

### The consequence panel

A **text box**. It explains what blended CAC *would* have read on those days (£6.75 / £6.33
against a true ~£11.50). You cannot show that from the model, because the model correctly
prevents it — which is the point.

---

## Validate first

Before building anything, load the model and check these against `docs/spec.md` §1.6:

| Measure | Expected |
|---|---:|
| `[Net Revenue]` | £1,211,689.88 |
| `[Contribution Margin]` | £855,863.08 |
| `[Margin %]` | 70.6% |
| `[Orders]` | 25,720 |
| `[New Customers]` | 20,284 |
| `[Ad Spend]` | £247,493.29 |
| `[Gap Days]` | 2 |
| `[LTV / CAC]`, 2024-07 cohort | 4.42x |
| `[LTV / CAC]`, 2025-06 cohort | 2.09x |
| `[Repeat Rate 90d]`, 2024-07 cohort | 31.8% |
| `[Repeat Rate 90d]`, 2025-03 cohort | 0.0% |
| `[Channel CAC]`, tiktok | **blank** |

A mismatch means the measure is wrong, not the warehouse — every one of those figures is
guarded by a dbt test.
