# Build log — plan: docs/plans/2026-10-13-marketing-reporting-extension.md

Scope: marketing spend, budget and Finance reconciliation on **synthetic** Finance inputs,
plus CI and a formula-driven Excel month-end pack.
Branch: `claude/trusting-cray-2dahbe` (the session's designated branch; the plan names
`feat/marketing-reporting`, see Setup rulings).
Branch base: 2a224fc

## Setup rulings

Ruling: develop on `claude/trusting-cray-2dahbe`, not `feat/marketing-reporting`. The work runs
in a managed cloud session that is pinned to that branch. Cost if wrong: a branch rename before
the PR; no content changes.

Ruling: the plan's commands are PowerShell (`.\venv\Scripts\...`). This session runs on Linux,
so the same commands run as `venv/bin/python` and `../venv/bin/dbt`. Only the paths differ.
Cost if wrong: none; the behaviour is identical.

## Task 0 — Baseline

### Environment findings (before any change)

| # | Finding | Effect |
|---|---|---|
| E1 | `requirements.txt` cannot be installed as committed: `streamlit==1.50.0` requires `pandas<3`, but the file pins `pandas==3.0.5`. Both `pip` and `uv` refuse to resolve it. | A clean install (and CI) fails at `pip install`. |
| E2 | `pyarrow` is not in `requirements.txt`, but `tests/test_detectors_integration.py` reads Parquet through pandas. Without it, 20 integration tests error with "Unable to find a usable engine". | A clean checkout gets 30 passed and 20 errors, not 50 passed. |
| E3 | `dbt deps` reaches `hub.getdbt.com`, which this session's network policy blocks. This is specific to the session, not the repo. `dbt_utils` 1.4.1 (the locked version) was cloned from GitHub into the gitignored `dbt/dbt_packages/` as a local workaround. | None for the repo; GitHub Actions can reach the hub. |

The baseline numbers below were taken with every requirement except streamlit installed, plus
`pyarrow`.

### Baseline numbers (actual)

| Check | README / plan | Actual |
|---|---|---|
| `dbt build` models | 20 | 20 |
| `dbt build` tests | 94 | 94 (PASS=113 WARN=1 ERROR=0 over 114 nodes) |
| Warning | — | `assert_source_date_completeness`, 2 rows. It is configured `severity='warn'` by design (Meta is missing 2025-03-15 and 2025-03-16). |
| `pytest` | 50 | 50 passed (after E1/E2 workarounds) |
| `mart_daily_trading` at cursor 2025-01-31 | 860 rows, max 2025-01-31 | 860 rows, max 2025-01-31 |
| `mart_daily_trading` at default cursor | — | 1460 rows, 2024-07-01..2025-06-30 |

### Recon notes (Task 0 Step 3)

- **Sources.** `_sources.yml` declares source `raw` with
  `meta.external_location: "read_csv_auto('{{ var('data_dir') }}/{name}.csv')"`. Each CSV is
  read in place and nothing is copied into the warehouse. The finance source group can use the
  same pattern with `{{ var('data_dir') }}/finance/{name}.csv`.
- **`data_dir`.** It is a dbt var, default `'../data'`, relative to the `dbt/` project directory.
  To override it: `--vars "{data_dir: /abs/path/to/copy}"`. An absolute path works because it
  goes straight into `read_csv_auto`.
- **Spend columns.**
  - Meta: `spend` (GBP, 2dp).
  - Google: `cost_micros` (÷ 1e6).
  - `stg_ads_daily` unions both into `spend DECIMAL(12,2)`. `fct_ad_spend_daily` keeps `spend`
    at campaign × day grain with `platform`, `campaign_id`, `ad_date` and `available_on`.
- **Channel values.**
  - `fct_ad_spend_daily.platform` is in {`meta`, `google`}.
  - `mart_daily_trading.channel` is in {`meta`, `google`, `tiktok`, `unattributed`}.
  - TikTok has no cost file.
- **Spend availability lag.** Exactly 1 day on both platforms. `_weld_synced` is the event date
  + 1 day at 00:00. It is carried as `fct_ad_spend_daily.available_on` (the date of
  `synced_at`), and in `mart_daily_trading` as `spend_available_on`.
- **As-of mechanism.** The `as_of_filter(sync_col, event_col)` macro keeps a row when
  `date(coalesce(try_cast(sync_col as timestamp), event_col)) <= as_of_date`. It is applied in
  staging, so every downstream view sees only rows visible at the cursor.
  - Default cursor: `2025-07-01`.
  - Staging views bake `var('as_of_date')` into the view text at build time. A two-cursor
    check must therefore re-run `dbt build`/`run` with `--vars`, not just `dbt show`. This is
    gotcha #2 in `docs/BUILD-LOG.md`.
- **Date range.**
  - Ads: 2024-07-01 to 2025-06-30 on both platforms. Verified.
  - Meta: 2,178 rows = 6 campaigns × 363 days. It has no rows for 2025-03-15/16.
  - Google: 1,825 rows = 5 × 365.
  - Total spend in the full period: Meta GBP 143,924.77 and Google GBP 103,568.52.
- **`dim_campaign`.** 11 campaigns (6 Meta and 5 Google). `funnel_stage` is parsed from the
  campaign name. Not needed at channel grain.
- **`_marts.yml`.**
  - Grain tests use `dbt_utils.unique_combination_of_columns` with nested `arguments:`.
  - The newer `data_tests:` syntax is used throughout. Gotcha: the older `tests:` form raises
    deprecation warnings.
- **`export_marts.py`.**
  - It holds a hard-coded `EXPORTS` list of `(schema, table)` and writes `data/marts/<table>.parquet`.
  - It uses `ORDER BY ALL` for deterministic output.
  - It exits 1 if any export has zero rows.
  - Schemas are `main_marts` and `main_core`.

## Task 1 — README inconsistencies

The code is the authority, not the README.

| Claim | Before | Measured | Action |
|---|---|---|---|
| Detector count | "ten" and "10" in two places, "all nine" in one | 10: ten keys under `detectors:` in `config/detectors.yml`, and ten `@register` calls in `engine/detectors/*.py` | "all nine" → "all ten" |
| Backtest cursors | 246, 245 and 365 | **246**. `engine.backtest` prints "2024-10-29 to 2025-07-01 (246 cursors …)". The replay starts after the warm-up, not on day 1. | 245 → 246, and both "365" mentions → 246 |
| Finding 1 | "LTV/CAC 4.42× → 2.43×. Cost +53%, value −29%" | 4.42× → 2.43× confirmed. Cost **+29%** (£9.68 → £12.47), value −29% (£42.81 → £30.24) | "+53%" → "+29%" |
| Finding 3 | "Meta CPC £0.42 → £1.02 (+144%)" | £0.41715 → £1.01699 = **+143.8%** | No change. +144% is right. |

Ruling: Finding 1 was a stale number, not a different definition. The +53% is
£9.68 → £14.84, the June 2025 cohort's CAC. It belonged to an earlier draft whose headline ran to
2.09× at the June 2025 cohort. That cohort's 60-day window has not closed, so `docs/spec.md`
§1.6 already withdrew that endpoint. The endpoint moved to 2025-04 (2.43×), but the cost delta
was never updated. The ratio is internally consistent only with +29%:
4.42 × (0.706 / 1.288) = 2.42.
- The finding's meaning is unchanged: the ratio is the same, and cost and value still both moved
  against the business.
- No STOP needed.

Cost if wrong: the README understates the cost rise. The ratio, the spec and the measures all
agree with +29%.

Ruling: the plan's hint that the CPC change is "closer to +143%" comes from the rounded
endpoints (1.02 / 0.42 = 2.429). The unrounded monthly CPCs give 2.438, so +144% stays.
Cost if wrong: one percentage point in a headline.

Out-of-scope inconsistencies found (not edited; `powerbi/` and `engine/` are off limits):
- `powerbi/…/p1_note/visual.json`: "Acquisition got 53% more expensive … 4.42x to 2.43x". It has
  the same stale +53%.
- `powerbi/…/#Measures.tmdl`, the `LTV / CAC` measure description: "Fell 4.42x to 2.09x …
  +53%". It quotes the withdrawn censored endpoint.
- `engine/backtest.py` docstring: "a full 365-day replay". The run is 246 cursors.
- Timing. The README says "~25s" for the backtest and "~100ms" per cursor. In this session it
  measured 67.6 s and 275 ms per cursor. That depends on the machine, so the README is left as is.

Queries (run from `dbt/`, against the default-cursor build):

```sql
-- Finding 1: cohort CAC uses the Power BI 'Cohort CAC' definition (complete-spend days only).
-- LTV is the 60-day margin LTV over fully exposed cohorts.
with cac as (
    select date_trunc('month', date_day)::date as m,
           sum(ad_spend)      filter (where ad_spend_is_complete) as spend,
           sum(new_customers) filter (where ad_spend_is_complete) as nc
    from main_marts.mart_daily_trading group by 1
), ltv as (
    select cohort_month as m, sum(cum_contribution_margin) / sum(cohort_size) as ltv60
    from main_marts.mart_ltv
    where horizon_days = 60 and has_full_exposure
    group by 1
)
select l.m, round(c.spend / c.nc, 2) as cohort_cac, round(l.ltv60, 2) as ltv60,
       round(l.ltv60 / (c.spend / c.nc), 2) as ltv_cac
from ltv l join cac c using (m) order by 1;
-- 2024-07: 9.68 / 42.81 / 4.42    2025-04: 12.47 / 30.24 / 2.43

-- Finding 3: Meta CPC from summed components, by month.
select date_trunc('month', ad_date)::date as m, sum(spend) / sum(clicks) as cpc
from main_core.fct_ad_spend_daily where platform = 'meta' group by 1 order by 1;
-- 2024-07: 0.41715   2025-06: 1.01699   change +143.8%
```

## Task 2 — CI

Ruling: fix E1 and E2 (see Task 0) in the same commit as the workflow, because CI cannot pass
without them. The user approved this.
- **Keep pandas 3.0.5 and raise streamlit to 1.65.0.** Downgrading pandas would move the
  existing models and the 50 tests onto a different pandas, which risks changing existing
  output.
- **Pin pyarrow at 25.0.1, not the latest 26.0.0.** streamlit 1.65.0 requires
  `pyarrow<26`.
- **Checks run:**
  - `uv pip check`: all compatible.
  - `app.py` under streamlit 1.65 via `streamlit.testing.v1.AppTest`: no exceptions.
    `use_container_width` deprecation warnings only.

Cost if wrong: the app relies on behaviour that changed between 1.50 and 1.65. The smoke test
covers the first render only. Task 9 re-checks the app.

Ruling: CI runs `scripts/export_marts.py` between `dbt build` and `pytest`. The 20 integration
tests skip when `data/marts/*.parquet` is absent. Before the export, pytest reported
"30 passed, 20 skipped", which would let CI go green without them. Cost if wrong: one extra
step of about 2 s.

Clean-checkout proof: `git clone .` of commit 1249e13 into a scratch directory, a fresh
Python 3.12 venv, and `pip install -r requirements.txt`. Then `dbt build` gives PASS=113 WARN=1
ERROR=0 (20 models, 94 tests), the export writes 10 files, and pytest gives **50 passed**.
- This matches Task 0.
- No hidden dependency on untracked files.
- One exception: this session cannot reach `hub.getdbt.com` (E3), so `dbt_packages/` was copied
  in rather than fetched by `dbt deps`. `dbt deps` itself is proven only by the first GitHub
  Actions run.

Triggers are push to `main` and pull requests, as the plan says. A push to this feature branch
therefore does not run CI. The first run happens when a PR is opened.

## Task 3 — Synthetic Finance inputs

Output: 37 budget rows (36 original + 1 revised), 37 invoice lines (24 platform + 12 agency
retainer + 1 one-off) and 48 allocation rows (24 × 100% platform + 12 × 60/40 agency).
Two runs with seed 20240701 give identical SHA-256 hashes on all three files.

Ruling: delivered spend inside the generator is computed in DuckDB with the **same per-row cast**
as `stg_ads_daily`: `cast(spend as decimal(12,2))`, and `cast(cost_micros / 1e6 as decimal(12,2))`.
50 Google rows have sub-penny `cost_micros`, so summing in Python from the raw micros would
drift by pennies from what the warehouse reports. Cost if wrong: the "within tolerance" months
would carry penny noise that does not come from the invoice.

Ruling: the above-tolerance recon case is Meta 2025-03, not an arbitrary overbilling. The
synthetic invoice bills the two days missing from `meta_ads_daily.csv` (03-15/16), estimated
as the mean of 03-14 and 03-17. The exception is then explained by a real property of the
extract and ties to the existing data-quality finding (README finding 6). Cost if wrong: a
reader takes the exception for real overbilling. The data README and the line description both
say what it is.

Ruling: no TikTok invoices. With no invoice and no cost file, recognised expense is NULL. That
is the cleanest test of "never a 100% underspend". Cost if wrong: none for this scope.
`billed_channel` has no TikTok value in the plan either.

Ruling: the 60/40 split is applied to all 12 agency retainers, not one. A one-off split would
leave `meta`/`google` month rows with and without fee content, which makes the series
inconsistent. Cost if wrong: none; scenario 3 is still findable at 2024-07 (`IL-0003`).

Ruling: no column changes from the plan. Media lines and fee lines are told apart by
`billed_channel` (`meta`/`google` = platform media, `agency` = fees), which already exists.

## Task 4 — Staging and core models

### Plan defect, ruled by the user: option A (media and fees kept apart)

As written, the plan set `recognised = GREATEST(delivered, invoiced)` and compared total invoiced
with delivered, while also allocating the agency retainer 60/40 to `meta`/`google`. Delivered spend
is media only. The literal rule therefore:
- made every Meta and Google month a +15–20% recon "exception";
- made a month's recognised expense depend on which invoice arrived first. With the agency invoice
  in and the platform invoice not yet received, the fee disappeared into the accrual
  (Meta 2025-01: £8,805.35, then £10,478.05 once the platform invoice landed).

Decision (user-approved): `cost_type` (`media` from platform-billed lines, `fee` from agency-billed
lines) splits `invoiced_amount` into `media_invoiced + fee_invoiced`.
- `estimated_unbilled_accrual = MAX(delivered − media_invoiced, 0)`.
- `recognised_expense = GREATEST(delivered, media_invoiced) + fee_invoiced`.
- The recon compares media only.
- The singular test is renamed `assert_recognised_equals_greatest_of_delivered_and_media_invoiced_plus_fees`.

Cost if wrong: fees are recognised only on receipt, never accrued, so a month whose agency invoice
is late understates fees until it arrives. That is a stated limitation; there is no contract
data to accrue from.

### Other rulings

Ruling: allocation visibility is enforced in `fct_invoice_allocated`, not in
`stg_invoice_line_allocations`. The README's staging rule forbids cross-source joins in staging,
and allocation rows have no date of their own. The fact starts from the already-filtered visible
lines, so an allocation can never appear before its line. Cost if wrong: none for correctness.
`stg_invoice_line_allocations` on its own shows all allocations at any cursor, which is harmless
because nothing reads it without the line.

Ruling: the remainder row is `ROUND(amount × Σpct, 2) − Σ(other rounded rows)`, not
`amount − Σ(other rows)`. When the allocations sum to 100% the two are identical, which is the
plan's rule. When they don't (e.g. 60/30), the plan's literal version silently hands the missing
10% to the last channel, so no data corruption could ever make
`assert_invoice_allocation_sums_to_line` fail. With this version a misallocation stays visible
and the test catches it. Cost if wrong: none; it is strictly stricter.

Ruling: `invoiced_amount`, `media_invoiced` and `fee_invoiced` are 0.00, not NULL, when no invoice
has been received. "No invoice received yet" is a known fact at the cursor, not missing data. It
is what makes `unbilled` and the accrual computable. `delivered_spend`, `estimated_unbilled_accrual`
and `recognised_expense` stay NULL where the input is genuinely unknown. Cost if wrong: a reader
takes TikTok's 0.00 invoiced for spend. Its `recognised_expense` and `delivered_spend` are NULL
and its `data_status` is `no_cost_source`.

Ruling: `data_status` gains `source_gap` (complete month, fewer spend days than calendar days)
and `unallocated` (the bucket row), alongside the plan's `partial_month` and `no_cost_source`, plus
`complete`. Meta 2025-03 is a source gap. Its delivered spend is understated by the two days the
extract never received, and a status of `complete` would hide that. It is computed from
`fct_ad_spend_daily`'s own day count, not from `mart_data_quality`, so core never depends on a mart.
`partial_month` means `month_end >= as_of_date`: the month's last day of spend is not yet visible
(1-day lag). Cost if wrong: an extra status value for downstream consumers to handle;
accepted_values pins the set.

Ruling: the month spine is `dim_date`'s months, so a month appears once its first day is in the
date spine. Budgets approved for a month that has not started are visible in staging but have no
row in the monthly fact yet. Cost if wrong: the budget mart cannot show next month's approved
budget before the month starts. Acceptable for a month-end view.

### Proving the singular tests can fail

The data was copied to the scratchpad, never edited in place, and passed with
`--vars "{data_dir: <copy>}"`.

| Test | Corruption | Result |
|---|---|---|
| `assert_invoice_allocation_sums_to_line` | `IL-0003` google 0.4000 → 0.3000 | FAIL 1 |
| `assert_no_double_counted_invoice_line` | duplicated the `IL-0003,meta` allocation row | FAIL 1 (models run first, then the test; under `dbt build` the upstream unique test fails and SKIPs it) |
| `assert_service_period_within_one_month` | `IL-0001` service end 2024-07-31 → 2024-08-05 | FAIL 1 |
| `assert_recognised_equals_greatest_of_delivered_and_media_invoiced_plus_fees` | **model mutation**: the plan's literal rule `GREATEST(delivered, media + fee)` | FAIL 12 |
| `assert_missing_spend_is_null_not_zero` | **model mutation**: `coalesce(delivered_spend, 0)` | FAIL 13 (12 TikTok months + the unallocated row) |

Ruling: the last two are proven by mutating the model, not the data. Both are identities of model
logic: no input data can make TikTok acquire a cost source, or make the recognised formula
disagree with itself. A data-only proof would be impossible, not merely skipped. The mutations
are the realistic regressions: reverting to the plan's rule, and coalescing NULL to zero. The
model was restored byte-for-byte (`cmp` clean) and the warehouse was rebuilt at the default cursor.

### Results

- `dbt build` at the default cursor: PASS=149 WARN=1 ERROR=0. That is **25 models and 125 tests**
  (was 20 and 94); the warning is the existing designed one.
- `pytest`: 50 passed.
- Anchors unchanged: `mart_daily_trading` has 860 rows, max 2025-01-31, at cursor 2025-01-31, and
  1,460 rows at the default cursor.

The new models change with the cursor:

| | Cursor 2025-01-31 | Default cursor (2025-07-01) |
|---|---|---|
| Visible invoice lines | 18 (max received 2025-01-06) | 45 allocated rows (June invoices not yet received) |
| Meta 2025-01 | delivered £8,531.24 (through 01-30), `partial_month`, fully accrued | complete, invoiced |
| Budgets | 2025-02 visible (approved 01-20) | — |

At cursor 2024-11-07, Google 2024-11 has only the original £11,500 budget; the revision is
approved 11-08.

## Task 5 — Marts

### Plan defect, ruled by the user: option 1 (budgets are media budgets)

After option A, `recognised_expense` included agency fees. The synthetic budgets had been sized
against media only, so variance on the plan's literal definition put 11 of 12 Meta months over
budget (average +14.1%) and 11 of 12 Google months over (+10.2%):
- scenario 1 (Meta 2025-05) was indistinguishable from the rest;
- pacing, which can only see media, measured a different thing from the budget mart.

Decision (user-approved): the synthetic budgets are **media budgets**.
- `fct_marketing_cost_monthly` gains `recognised_media_expense = GREATEST(delivered, media_invoiced)`
  (`media_invoiced` if delivered is NULL and a media line exists, else NULL).
- The budget mart's variance is `budget_variance = recognised_media_expense − approved_budget`.
  The sign convention is unchanged: positive means overspend.
- `fee_invoiced` and the all-in `recognised_expense` are shown, not compared. Fees are unbudgeted
  in this data.

Result: Meta is over budget in 6 of 12 months (average +0.5%) and Google in 4 of 12 (−1.9%).
Meta 2025-05 is the largest overspend at +9.1%.

Cost if wrong: the plan's global wording "variance = recognised_expense − budget" now reads
"recognised *media* expense". A reader who assumes the budget covers fees understates the
overspend by the fee. The mart description, the column name and the README say "media budget".

### Other rulings

Ruling: pacing rows cover **every calendar day of each month** in the date spine, not just days up
to the cursor. At a mid-month cursor the plan still runs to month-end (needed for the chart and
for "plan sums to budget"), and days after the cursor are `not_yet_visible` with NULL delivered.
Cost if wrong: 3 channels × up to 30 extra rows per run, all clearly flagged.

Ruling: the plan is rounded **cumulatively**: `planned_cumulative = ROUND(budget × W(d), 2)`, and
`planned_spend` is the day-on-day difference. A month's plan therefore sums to its budget to the
penny, with no remainder day. `W(n)` is pinned to 1 so float error cannot leave a penny.
- An unknown pacing profile returns NULL rather than defaulting to linear.
- The front-loaded profile is half linear and half straight-line decline: day 1 plans about 1.5× an
  average day, the last day about 0.5×. The formula is documented in `macros/pacing_weights.sql`.

Cost if wrong: none for the totals. The front-loaded shape is a documented judgement.

Ruling: `spend_status` separates `not_yet_visible` (the cursor) from `missing` (the source never
delivered: Meta 2025-03-15/16). On `missing` days the cumulative carries flat; on
`not_yet_visible` and `no_cost_source` it is NULL. Cost if wrong: a reader sees a flat cumulative
on gap days. The status says why, and the budget mart flags the month `source_gap`.

Ruling: `run_rate_7d` is the mean over the days **with spend** in the trailing 7 calendar days, not
sum ÷ 7. A gap day therefore does not drag the run rate down as if spend were zero. The window
crosses month boundaries. The forecast on row d uses only data up to d, so the 2025-05-14 row
reproduces the mid-month view the scenario describes: £13,539.18 against a £12,750 budget. The
generator printed £13,539.17; it truncates rather than rounds.

Ruling: the recon compares **media only**. `difference = media_invoiced − delivered_spend`, which
is the plan's "invoiced minus delivered" applied to the comparable part. `unallocated_invoiced_amount`
is the amount on the `unallocated` row and 0.00 on channel rows, so it sums correctly.
`recon_status` precedence: `unallocated_cost`, `no_cost_source`, `unbilled` (no media invoice
yet), `within_tolerance`, `exception`.

Ruling: `assert_all_five_scenarios_present` also checks the recon exception. It is warn-only by
plan. At cursor 2025-01-31 it warns on 3 scenarios that have not happened yet (the May
overspend, the March exception, the February unallocated line). That is correct, and is why it
cannot be an error.

### Proving the singular tests can fail

| Test | How | Result |
|---|---|---|
| `assert_pacing_cumulative_monotonic` | data copy: Meta 2024-07 budget → −10,500.00 | FAIL 30 |
| `assert_pacing_plan_sums_to_budget` | data copy: Google 2024-08 `pacing_profile` → `back_loaded` | FAIL 1 |
| `assert_budget_and_recon_agree` | model mutation: budget mart recomputes accrual as `delivered − media_invoiced` | FAIL 10 |
| `assert_recon_ties_to_ad_spend` | model mutation: cost fact drops day 1 of each month from delivered | FAIL 24 |
| `assert_budget_variance_identity` | model mutation: variance sign flipped | FAIL 24 |
| `assert_all_five_scenarios_present` | data copy: TikTok budget rows removed | WARN 1, naming `4_tiktok_null_not_zero` |
| `assert_recognised_…_plus_fees` (re-proven for the new media branch) | model mutation: `recognised_media_expense = delivered_spend` | FAIL 10 |

The mutation-proven tests compare two readings of the same upstream fact. No data change can make
them disagree, only a code change can, so a mutation is the honest proof. Every mutation was
reverted (`cmp` clean). The `--store-failures` audit schema was dropped and the warehouse rebuilt
at the default cursor.

### Results

- `dbt build` at the default cursor: PASS=180 WARN=1 ERROR=0. That is **28 models and 153 tests**
  (baseline 20 and 94); the one warning is the existing designed one.
- `export_marts.py`: 13 files, including the 3 new marts (37 / 1,095 / 37 rows). The zero-row check
  passes.
- `pytest`: 50 passed.
- Anchors unchanged: 860 rows, max 2025-01-31, at cursor 2025-01-31; 1,460 rows at the default.

## Task 6 — Excel month-end pack

Ruling: the three new marts gain an `as_of_date` column, the cursor the build ran at. Parquet
carries no build metadata, and the pack (and the Task 9 page) must stamp what the warehouse
knew. Only the new marts change. Cost if wrong: one constant column per row.

Ruling: two committed samples, not one. 2025-06 is the actual close at the default cursor
(unbilled, a £20,489 accrual). 2025-03 shows the exception path (the Meta invoice is +6.9% against a
`source_gap` month). The plan's example month 2025-01 reconciles quietly at the default cursor,
so it would demonstrate nothing. Cost if wrong: one extra 10 KB file.

Ruling: `Month_End` pulls the pasted figures with direct row references and `SUMIFS`, and computes
accrual, recognised expense, variance, recon difference and status with `IF`, `MAX`, `ROUND` and `ABS`.
Every formula carries a blank through as blank: `""` is tested before any arithmetic, because
Excel treats an empty cell as 0. `INDEX/MATCH` is used in `Commentary` to find the channel with the
largest variance and the exception. No `LET`, `LAMBDA`, `XLOOKUP` or spilling functions. The total
variance % is a ratio of sums (total variance ÷ budget of channels with known spend), via a helper
column, not an average of percentages.

Ruling: `Commentary` sentences are formulas, not text written by Python. They update if `Inputs`
or the tolerance change, which keeps the "logic lives in formulas" rule. The exception sentence
checks `data_status`. On a `source_gap` month it says the missing days may explain the gap and
recommends getting them before querying the invoice, rather than blaming the vendor.

Ruling: the committed workbooks are recalculated in LibreOffice (`recalc.py`), so previewers and
`pandas` see values. Formulas, fonts and fills survive the rewrite; this was checked by reloading.
Cost if wrong: none; Excel recalculates on open regardless.

Verification:
- `tests/test_month_end_pack.py` (5 tests) builds a pack and asserts:
  - every `Month_End` figure, `Checks` result and `Commentary` sentence is a formula;
  - `Inputs` holds only values;
  - TikTok spend and variance are blank, not 0;
  - every sheet says SYNTHETIC.
- pytest: **55 passed** (50 + 5).
- LibreOffice recalculation of both samples: 93 formulas, 0 errors, all 23 `Checks` TRUE.
- Tolerance drives the logic. Setting `B3` to 10 flips Meta 2025-03 from `exception` to
  `within_tolerance`, and the commentary changes. The status checks then read FALSE, as they
  should, because the mart was built at 1%.

Not verified here: opening in Microsoft Excel itself. That is the user's STOP check.

## Task 7 — Publish marts to BigQuery

Ruling: publish all nine `mart_*` tables, not just the three new ones. The plan allowed it "if
cheap". The largest table has 8,760 rows. Dimensions and `fct_ad_spend_daily` are left out:
they are Power BI inputs, not reporting marts. Cost if wrong: none.

Ruling: `decimal_target_types=["NUMERIC", "BIGNUMERIC"]` on the load. The existing marts carry
`DECIMAL(38,2)` sums. That is 36 integer digits, more than NUMERIC's 29, so a NUMERIC-only load
would be rejected. Cost if wrong: none; BigQuery picks NUMERIC where it fits.

Ruling: the script reads a `.env` with a ten-line parser rather than adding `python-dotenv`. The
real environment wins over the file. `--dry-run` never imports the BigQuery client, so it works
without `requirements-bq.txt`. Cost if wrong: an exotic `.env` (multi-line values, `export`
prefixes) is not parsed. `.env.example` uses only plain `KEY=VALUE`.

Ruling: the BigQuery client is pinned in `requirements-bq.txt`, not `requirements.txt`. CI and
the core build never need it. Cost if wrong: none.

Verification:
- `--dry-run` lists 9 tables whose row counts match the Parquet files: 37, 1,095, 37, 1,460,
  8,760, 132, 144, 318 and 1,095.
- With `requirements-bq.txt` installed, `pip check` is clean and the `LoadJobConfig` builds.
- Without credentials the script exits 1 with a clear message.
- `.env` is gitignored, and no key file is in the repo.

**Not run: a real load.** This machine has `BQ_PROJECT_ID` set but no
`GOOGLE_APPLICATION_CREDENTIALS` and no gcloud account, so per the plan it stays dry-run only. The
README says so.
