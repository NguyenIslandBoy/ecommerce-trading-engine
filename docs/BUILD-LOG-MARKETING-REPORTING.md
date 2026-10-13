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
