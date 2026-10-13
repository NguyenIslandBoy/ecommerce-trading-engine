# eCommerce Trading Engine

[![CI](https://github.com/NguyenIslandBoy/ecommerce-trading-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/NguyenIslandBoy/ecommerce-trading-engine/actions/workflows/ci.yml)

Twelve months of a D2C brand's operations turned into a tested metric spine, a
detection layer that separates real commercial movements from measurement
artifacts, and a recommendation layer that sizes each action to how much its
evidence can actually bear.

| Layer | State |
|---|---|
| **1. Data foundation** | Built — 28 dbt models, 154 data tests, point-in-time correct |
| **2. Signal detection** | Built — 10 detectors, replayed across 246 cursors |
| **3. Recommendation & simulation** | Built — Monte Carlo, autonomy gate |
| **Marketing reporting** | Built — budget vs actual, pacing and invoice reconciliation on **synthetic** Finance inputs ([below](#marketing-spend-budget-and-finance-reconciliation-synthetic-finance-inputs)) |
| **Surfaces** | Streamlit app (`app.py` + marketing page), Power BI report (`powerbi/`), Jupyter walkthrough (`notebooks/`), Excel month-end pack (`reports/month_end/`) |

**Start here:** [`notebooks/walkthrough.ipynb`](notebooks/walkthrough.ipynb) — the
narrative version of everything below, executed, with charts. It answers the
commercial questions first and shows the working underneath.

---

## What the data says

Six findings. **Four of them read backwards on first look**, which is why the
detection layer is built more to stay quiet than to fire.

| | Finding | Reading |
|---|---|---|
| 1 | **LTV/CAC 4.42× → 2.43×** across fully-exposed cohorts | Real. Cost +29%, value −29%; neither alone alarming |
| 2 | **90-day repeat rate 31.8% → 0.0%** | Real, and hidden by a flat 24% blended rate |
| 3 | **Meta CPC £0.42 → £1.02** (+144%) | Real, but only *half* is yours to fix |
| 4 | **AOV £49.69 → £44.38** (−11%, ex-VAT) | Real — mix shift, not discounting (discount rate steady at 3.5–4.1%) |
| 5 | **Email opens −20% across all six flows** | **Artifact.** Conversion held. Do not act |
| 6 | **Meta spend "collapsed" 15–16 March** | **Data quality.** The file never arrived |

### The one that matters most

Ask "are customers coming back?" and the obvious metric says yes — repeat orders
have been ~24% of all orders all year. That number is the most dangerous thing
in the dataset.

It measures the whole book. Measured per *acquisition cohort*, the share
returning within 90 days runs:

```
2024-07  2024-08  2024-09  2024-10  2024-11  2024-12  2025-01  2025-02  2025-03
 31.8%    25.2%    15.8%     9.7%     2.4%     0.2%     0.2%     0.2%     0.0%
```

Every cohort above has **full 90-day exposure** — this is measured, not
censoring. Censoring explains only 2025-04 onward, where the median 100-day gap
to a second order means the window has not closed.

Both numbers are correct; the denominators differ. The blended rate stays
healthy because the Jul–Nov 2024 cohorts are still buying. **Essentially nobody
acquired in 2025 returns.** It is a lagging indicator wearing a current one's
costume — and it will look fine right up until it doesn't.

Commercially that inverts the obvious response: acquiring more customers into a
book that does not retain makes the problem larger. The engine's recommendation
here is *investigate*, not *spend differently*.

### Why Meta got expensive, and why it changes the answer

CPC is auction price ÷ click-through. The split decides the action:

| | Change | Fixable by changing the ads? |
|---|---:|---|
| CPM (auction price) | **+56%** | No — budget or targeting |
| CTR (click-through) | **−36%** | Yes — creative fatigue |

Both moved, roughly evenly. So the recommendation is two-pronged: refresh
creative for the half that responds to it, and size any budget reallocation to
the auction-attributable half only. "CPC is up, move budget" would be moving
money for the wrong reason.

---

## Run it

Python 3.12 — dbt-core does not support 3.14.

```bash
uv venv --python 3.12 venv
uv pip install --python venv/Scripts/python.exe -r requirements.txt

cd dbt && ../venv/Scripts/dbt.exe deps --profiles-dir .
          ../venv/Scripts/dbt.exe build --profiles-dir .   # 28 models, 154 tests
cd .. &&  venv/Scripts/python.exe scripts/export_marts.py  # marts -> Parquet

venv/Scripts/python.exe -m engine.run          # detect at the latest cursor
venv/Scripts/python.exe -m engine.backtest     # replay 246 cursors, ~25s
venv/Scripts/python.exe -m pytest              # 55 tests
venv/Scripts/python.exe -m streamlit run app.py
```

Without `uv`: `python -m venv venv && venv/Scripts/python -m pip install -r requirements.txt`.

Rebuild the warehouse as it stood on any past date:

```bash
../venv/Scripts/dbt.exe build --profiles-dir . --vars '{as_of_date: 2025-01-31}'
```

That produces `mart_daily_trading` with `max(date_day) = 2025-01-31` and exactly
860 rows — 215 days × 4 channels. The whole warehouse respects the cursor, not
just the ad tables.

Neither the `.duckdb` file nor `data/marts/*.parquet` is committed. Both are
build outputs, reproducible in under a minute, and byte-identical across
rebuilds.

### Publishing the marts to BigQuery (optional, manual)

The warehouse is **built in DuckDB** by dbt. `scripts/publish_to_bigquery.py` only **publishes**
the exported marts to BigQuery so they can be queried there; it does not build anything natively
on BigQuery.
- **What it loads:** the nine `mart_*` Parquet files, one table each, into dataset
  `trading_engine_marts`, with `WRITE_TRUNCATE`.
- **Labelling:** the three `mart_marketing_*` tables are labelled as built on synthetic Finance
  inputs in their table descriptions.
- **Not in CI:** it is a manual step.

```bash
uv pip install --python venv/Scripts/python.exe -r requirements-bq.txt   # optional deps
cp .env.example .env    # set BQ_PROJECT_ID, GOOGLE_APPLICATION_CREDENTIALS, BQ_LOCATION (default US)
venv/Scripts/python.exe scripts/publish_to_bigquery.py --dry-run         # tables + row counts, no connection
venv/Scripts/python.exe scripts/publish_to_bigquery.py                   # load, then compare row counts
```

A real run re-reads each table's row count from BigQuery and exits non-zero on any mismatch with
the Parquet file. **Status:** the dry run has been executed. A real load has not been run from this
repo yet.

## Marketing spend, budget and Finance reconciliation (synthetic Finance inputs)

> **The Finance inputs are SYNTHETIC.** The budgets, vendor invoices and allocations in
> `data/finance/` are invented by a seeded generator (`scripts/generate_finance_inputs.py`, seed
> 20240701), sized against the real-shaped ad spend so the variances are plausible. Vendors are
> placeholders. Every figure below that involves a budget or an invoice inherits that.

**Questions it answers**
- Is each channel over or under its media budget this month?
- Is spend pacing to plan, and where will the month land?
- Do platform invoices agree with the spend the platforms reported?
- How much delivered spend has not been invoiced yet, and so must be accrued at month-end?

| Model | Grain | What it holds |
|---|---|---|
| `stg_marketing_budget`, `stg_vendor_invoice_lines`, `stg_invoice_line_allocations` | source rows | Cast to `DECIMAL(12,2)` / `DATE`. A budget is visible from `approved_on`, an invoice from `received_date`. |
| `fct_invoice_allocated` | invoice line × channel | Penny-exact allocation. A line with no allocation becomes an `unallocated` row and is never dropped. |
| `fct_marketing_cost_monthly` | month × channel | The single source of truth: delivered, media and fee invoiced, accrual, recognised expense, `data_status`. |
| `mart_marketing_budget_vs_actual` | month × channel | Latest visible media budget against recognised media expense. |
| `mart_marketing_pacing_daily` | date × channel | Planned against delivered cumulative spend, with a 7-day run-rate forecast. |
| `mart_marketing_invoice_recon` | month × channel | Media invoiced against delivered spend, with `recon_status`. |

**Accrual rule.** Delivered spend is media only, so platform media invoices and agency fees are
kept apart.
- **Before the final invoice:** `estimated_unbilled_accrual = MAX(delivered_spend − media_invoiced, 0)`
  and `recognised_media_expense = GREATEST(delivered_spend, media_invoiced)`. Only delivered media
  that has not been invoiced is accrued.
- **After it (the true-up):** once a platform media invoice covering the whole month is received
  (`is_media_final`), the invoice replaces the estimate. The accrual becomes 0 and
  `recognised_media_expense = media_invoiced`, whether the invoice came in above or below
  delivered spend. The recon `difference` is the size of the true-up.
- `recognised_expense = recognised_media_expense + fee_invoiced`.
- When delivered spend is unknown, the accrual is NULL and recognised expense is whatever has
  been invoiced, or NULL if nothing has.

**Variance convention.** `budget_variance = recognised_media_expense − approved_budget`. **Positive
means overspend.** The synthetic budgets are media budgets: agency fees are shown, not budgeted.

**Recon status.** In order of precedence:
1. `unallocated_cost`
2. `no_cost_source`
3. `unbilled`: no media invoice yet
4. `within_tolerance`: within `recon_tolerance_pct`, default 1.0%
5. `exception`

**The five scenarios** (plus one exception case), all built into the synthetic data and checked by
`assert_all_five_scenarios_present`:

| # | Scenario | Where |
|---|---|---|
| 1 | Forecast overspend | Meta 2025-05. The forecast on the 14th is £13,539 against a £12,750 budget; the month ends +9.1%. |
| 2 | Delivered in M, invoiced in M+1 | Every month. At the default cursor, June is `unbilled` and £20,489 is accrued. |
| 3 | Agency invoice split 60/40 Meta/Google | Every monthly retainer. |
| 4 | Missing cost source | TikTok has a budget but no cost file: spend and variance are NULL, never a 100% underspend. |
| 5 | Unallocated invoice line | 2025-02, £1,250. It surfaces as its own row. |
| + | Recon exception | Meta 2025-03, +6.9%. The synthetic invoice bills the two days missing from the Meta extract (finding 6). |

**Run it**

```bash
venv/Scripts/python.exe scripts/generate_finance_inputs.py        # regenerate data/finance (deterministic)
cd dbt && ../venv/Scripts/dbt.exe build --profiles-dir . && cd ..  # includes the marketing models
venv/Scripts/python.exe scripts/export_marts.py
venv/Scripts/python.exe scripts/build_month_end_pack.py --month 2025-06   # Excel pack
venv/Scripts/python.exe -m streamlit run app.py                    # then open "Marketing Budget"
```

- **Excel pack:** every figure in `Month_End` is a formula over the pasted mart rows, and the
  `Checks` sheet ties the workbook back to the marts. The committed samples recalculate with every
  check TRUE in LibreOffice and in Excel.
- **Worked example:** [`docs/worked-example-month-end-close.md`](docs/worked-example-month-end-close.md)
  replays January's close at two cursors and shows the accrual estimate against the final invoices.

**Limitations**
- **Synthetic Finance inputs.** No conclusion about real budgets or vendors can be drawn from them.
- **Channel grain only.** Invoices bill at account level and the orders carry no UTMs, so there are
  no campaign-level budgets.
- **No GL or journal entries.** The true-up restates the month's figures once its final invoice
  arrives; it does not post journals. A month invoiced in several partial-period invoices stays on
  the estimate until a full-month invoice arrives (none exist in this data). Fees are recognised
  on receipt and never accrued.
- **Simple run-rate forecast.** Delivered to date + 7-day average × days left. It ignores
  seasonality and planned changes.
- **No attribution or incrementality claims.** Budget variance says nothing about what the spend
  returned.
- **TikTok has no cost source.** Its spend, accrual and variance are unknown.

---

## What's in the repo

| Path | |
|---|---|
| `data/*.csv` | The seven source extracts. Synthetic, no PII |
| `data/finance/` | **Synthetic** Finance inputs (budgets, invoice lines, allocations), seeded generator in `scripts/generate_finance_inputs.py` |
| `dbt/` | The warehouse — 28 models, 154 tests, 3 macros |
| `engine/` | Layers 2 and 3 — detectors, simulation, backtest |
| `config/` | Every threshold, in YAML rather than in code |
| `app.py` | Streamlit: as-of slider, signals, simulated outcomes, backtest |
| `pages/1_Marketing_Budget.py` | Streamlit page: budget vs actual, pacing, invoice recon (synthetic Finance inputs) |
| `reports/month_end/` | Formula-driven Excel month-end packs (synthetic), built by `scripts/build_month_end_pack.py` |
| `.github/workflows/ci.yml` | CI: `dbt build`, mart export and `pytest` on every push to `main` and every PR |
| `powerbi/` | Two-page report, `.pbip` source so it diffs |
| `notebooks/` | The executed walkthrough |
| `scripts/verify_pit.py` | Proves the fast backtest equals a real rebuild |
| `docs/specs/…design.md` | The design doc, written before any code |
| `docs/BUILD-LOG*.md` | 71 decisions with their reasoning and cost-if-wrong |

---

## How it's built

**Warehouse** — `staging` may cast, rename and derive from a single source
column, but may **not** join across sources or aggregate. That constraint keeps
the point-in-time filter simple enough to trust. `core` is a star schema
carrying COGS and contribution margin. `marts` is the metric spine everything
downstream reads.

**Detection** — ten detectors, each returning a `Signal` with its evidence,
classification, attribution tier and confidence. Adjudication happens centrally
so all ten are judged on the same terms: outage suppression, then
Benjamini–Hochberg FDR at 0.10, then confidence as severity × tier reliability ×
window cleanliness.

**Recommendation** — Monte Carlo over every proposed action, 10,000 draws.
Outputs are a median, `P(margin gain)` and an 80% credible interval. Never a
point estimate.

---

## The decisions that matter

**Point-in-time correctness.** Every staging model that can filters on row
*availability* (`_weld_synced`) rather than event time. The lag was measured,
not assumed: orders land same-day on 100% of 26,553 rows, both ad sources and
email at +1 day on 100% of theirs.

`as_of_date` defaults to **2025-07-01** — the day *after* the period closes —
because an engine running on 2025-06-30 could not yet see that day's ad spend.
A consequence worth stating: the newest day of *every* historical rebuild has
orders and NULL spend, by design. A backtest hits that 246 times and must
discard it rather than read it as a gap.

**VAT comes out before margin goes in.** Source prices are VAT-inclusive
(`total_tax = subtotal_price / 6` exactly on all 25,720 non-cancelled orders)
while `products.cost` is ex-VAT. Subtracting one from the other directly
overstates margin by 6–8 points per variant and inflates every LTV and ROAS
downstream.

**NULL is not zero.** Meta reports no conversions — coerced to zero, its ROAS
reads 0.0 rather than "unknown". TikTok has no cost file — at £0 it would look
like free acquisition. On the two Meta gap days, dividing the spend that *did*
arrive by the customers acquired gives £6.75 and £6.33 against a true ~£11.72:
a 44% understatement that looks like good news.

**Censoring guards, in both directions.** A cohort that has not had 90 days to
return has not failed to retain. `assert_censored_ltv_is_null` fails if a
censored value is ever published; `assert_exposed_ltv_is_populated` fails if the
guard grows aggressive enough to hide real data. Getting the second one wrong is
just as bad, and less obvious.

**Statistics chosen for one January.** Theil–Sen slopes (≈29% breakdown point)
rather than least squares, Mann–Kendall rather than a t-test, MAD z-scores
rather than standard deviations. Day-of-week seasonality is removed;
**month-of-year is not, because it cannot be** — twelve months contains exactly
one January, so the month effect is perfectly confounded with trend.

Where a detector needed seasonal defence anyway, it measures *share of
catalogue*: against its own history, product velocity flagged 23 of 24 variants
in peak season, which is another way of saying it detected nothing.

**Two thresholds set by measurement, not taste.** `cac_trend` uses a 90-day
window because at 28 days the same series gives slope −0.17%/day at *p* = 0.42 —
no trend at all — while 90 days gives +0.25%/day at *p* = 2.5 × 10⁻⁸. A 28-day
window stays silent through the entire deterioration. And `email_engagement_decay`
tests co-movement as a *ratio* with a significance requirement, because a fixed
tolerance asks a different question of a 2% flow and a 50% one.

**Reversibility sets the ceiling; confidence sets the magnitude.** Ad spend can
be unwound tomorrow, so it can be automatic. An inventory purchase cannot, so it
is reviewed at *any* confidence. A third rule was added after watching the
engine misbehave: it recommended a creative refresh its own simulation gave a
13% chance of paying, median −£817. Confidence says the *signal* is real;
`P(gain)` says *acting on it* pays. Both must hold.

---

## Backtest

`venv/Scripts/python.exe -m engine.backtest` replays detection at all 246
cursors in ~25 seconds and scores it against hand labels in
`config/ground_truth.yml`.

| | |
|---|---|
| Recall | **100%** (6/6 labelled events) |
| Precision | 67% of distinct commercial signals map to a labelled event |
| Trap violations | **0 of 4** |

The traps matter more than the events. Anyone can build a detector that fires;
the test is whether it stays silent on four things that look exactly like
signals and are not — the Meta outage reading as a CAC improvement, censored
cohorts reading as a collapse, the January trough manufacturing a crisis, and
email decay that conversion never followed.

**On the brief's worked example.** *"Meta CAC has exceeded the 60-day LTV
threshold for 5 consecutive days"* does occur, and `cac_ltv_breach` fires on it:
£36.40 against £30.24, a 1.20× breach over 2025-06-26 to 06-30. The engine then
**declines to reallocate unattended**, and that refusal is deliberate rather than
a gap. A channel-level CAC rests entirely on last-click attribution, and 26.8% of
orders have no usable referrer — so it is Tier C, capped at 0.55 confidence, and
monitors instead of acting. The attribution-free blended CAC (£12.23) is nowhere
near the threshold, which is the more reassuring number and the one that would
have been allowed to act.

A 246-cursor replay is affordable only because `engine/pit.py` reconstructs any
cursor in ~100ms against the ~35s a real dbt rebuild takes.
`scripts/verify_pit.py` proves that shortcut: it rebuilds the warehouse at three
cursors and asserts the reconstruction is identical **on every column**.

Precision is deliberately harsh, and worth reading carefully: **any** commercial
signal that does not map to one of six labelled events counts against it,
including genuine findings nobody thought to label. Adding labels to match what
the detectors found would raise the number and mean nothing, so the labels have
been left where profiling put them.

> **Stated limitation.** The ground-truth labels are my own reading of the
> dataset, written before the detectors — but written by the same person who
> then built them. This measures **internal consistency, not external
> validity**. A perfect score is evidence the engine does what it was designed
> to do, not that the design was right.

---

## Assumptions

Everything here is a judgement I made, not something the brief or the data
stated. All are overridable in `config/`.

| Assumption | Why | If wrong |
|---|---|---|
| 60-day LTV horizon | Taken from the brief's own worked example | See the horizon caveat below |
| Peak season = Nov–Dec | Inferred from the order distribution | Seasonal handling misfires at the edges |
| FDR α = 0.10 | Missing a real CAC breach costs more than investigating a false one | More or fewer marginal signals |
| Tier C ceiling 0.55 | 26.8% of non-cancelled orders are unattributed | Last-click evidence could drive unattended action |
| 21-day reorder lead time | Placeholder — no supplier data exists | Stockout probabilities shift materially |
| `P(gain)` floor 0.55 | An action should be more likely to help than not | Marginal actions taken or skipped |

No business rules or thresholds were supplied — nothing states what LTV/CAC
counts as unhealthy, or what days-of-cover should trigger a reorder. That is
precisely why they live in `config/detectors.yml` and `config/actions.yml`
rather than in code, and why the report carries no RAG status.

---

## What the data cannot answer

Structural limits, not fixable with better code:

| Gap | Consequence |
|---|---|
| **Zero UTM parameters** across all 26,553 orders | Last-click referrer is the only attribution; 26.8% of orders unattributable. All channel figures are Tier C |
| **No TikTok cost file** | 9.0% of orders have no measurable acquisition cost. CAC is BLANK, never £0 |
| **Inventory is a current snapshot** | `days_of_cover` is meaningful only at the latest date, never historically |
| **Cost is a current snapshot** | Present-day unit cost is applied to every historical order, so any real cost change during the year is invisible and silently absorbed into margin trends |
| **`orders.email` ≠ `customers.email`** for 83% of orders | Must join on `customer_id`. `assert_no_email_join_used` fails the build if an email join would ever match >20% of orders |
| **Trailing windows start partial** | `velocity_28d` and the 8-week email means are unreliable for the first 27 days / 7 weeks. Detectors discard the warm-up |

### One finding that shapes everything downstream

**A fixed LTV horizon is not comparable across cohorts.** £173,758 of £1,211,690
net revenue (14.3%) arrives after day 90 — and almost all of it belongs to three
cohorts:

| Cohort | Revenue after day 90 | Share of that cohort |
|---|---:|---:|
| 2024-07 | £100,953 | 44.4% |
| 2024-08 | £57,461 | 36.1% |
| 2024-09 | £13,715 | 12.4% |
| 2024-10 | £760 | 0.8% |
| 2025-03 onward | £0 | 0.0% |

So a 60-day LTV badly understates early cohorts and is essentially complete for
recent ones. The truncation bias shrinks toward zero *as retention collapses*,
which flatters recent cohorts and **understates how far unit economics have
actually moved**. It is the censoring trap arriving through a different door.

---

## What I would build next

**Score the backtest against outcomes, not my own labels.** Lead time is
currently measured against an onset date I chose, which makes it partly a
measure of my labelling. Scoring against a commercial outcome would be far
stronger — it needs someone to define which outcome counts.

**Compare cohorts at equal observed age** rather than at a fixed horizon, per
the caveat above.

**Close the attribution gap at source.** UTM tagging and a TikTok cost export
would move a quarter of the order book out of Tier C, which is the single
largest constraint on what this engine is allowed to decide on its own.
