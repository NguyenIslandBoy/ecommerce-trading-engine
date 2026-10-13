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
