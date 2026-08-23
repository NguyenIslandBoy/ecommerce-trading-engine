# Build Log — Power BI Prep

Branch: `feat/data-foundation`. Two prerequisites for the Power BI report:
(1) give the headline "31.8% -> 0.0%" repeat-rate claim a home in the
warehouse, guarded by tests, and (2) export the marts to Parquet so the
report can read them without the (gitignored) `.duckdb` file.

Baseline measured before any change in this pass, from a deleted
`dbt/target/`: `dbt build` -> `PASS=109 WARN=1 ERROR=0 TOTAL=110`, **90**
test nodes (confirmed via `target/manifest.json` node count, matching the
build log's "N data tests" line). The task brief's stated baseline of "93
test nodes" does not match this measurement; PASS/WARN/ERROR/TOTAL and
every row count in the brief DID match exactly, so — consistent with how
the same brief/measurement mismatch was handled in
`docs/BUILD-LOG-FINAL-FIXES.md` — this is treated as a documentation slip
rather than something broken, and the pass criterion actually enforced is
"exactly 2 more test nodes than the true baseline" (90 -> 92).

Commit: `b7beb11` (Task 1). Task 2 committed separately below.

---

## Task 1 — rolling 90-day repeat rate in `mart_cohort_retention`

Added three cohort-level columns to `dbt/models/marts/mart_cohort_retention.sql`,
repeated across all 11 `months_since` rows per cohort (grain unchanged,
row count still 132):

- `repeat_within_90d` (BIGINT) — distinct customers in the cohort who
  placed a further, strictly-later, non-cancelled order within 90 days
  of their own first order date. Computed from `fct_order` joined back
  to each customer's own `first_order_date`, independent of the
  calendar-month bucketing `retention_rate` uses.
- `has_full_90d_exposure` (BOOLEAN) — `max(first_order_date)` **within
  the cohort** + 90 days <= the LAST DATE WITH DATA
  (`max(dim_date.date_day)`, i.e. 2025-06-30 — deliberately not
  `var('as_of_date')` = 2025-07-01, which sits one day past period close
  to absorb ad/email ingestion lag). Uses the cohort's *latest* joiner
  (worst case) as the exposure test, mirroring how `has_full_exposure`
  is keyed to the cohort's calendar-month boundary.
- `repeat_rate_90d` (DOUBLE) — `repeat_within_90d / cohort_size`, NULL
  wherever `has_full_90d_exposure` is false, exactly mirroring the
  `retention_rate` guard.

`dbt/models/marts/_marts.yml`'s `mart_cohort_retention` description now
documents both retention measures side by side and states which one
backs the README/design-doc headline figure. `README.md`'s "retention
trap" section, which previously stated the rolling figure "is not a
column in any mart," was corrected to point at `repeat_rate_90d`.

### Verification table (read directly from the built warehouse)

| cohort_month | cohort_size | repeat_within_90d | repeat_rate_90d | has_full_90d_exposure |
|---|---:|---:|---:|---|
| 2024-07 | 1879 | 597 | 0.3177 | true |
| 2024-08 | 1605 | 404 | 0.2517 | true |
| 2024-09 | 1742 | 275 | 0.1579 | true |
| 2024-10 | 1730 | 167 | 0.0965 | true |
| 2024-11 | 2280 | 55 | 0.0241 | true |
| 2024-12 | 2361 | 5 | 0.0021 | true |
| 2025-01 | 1197 | 2 | 0.0017 | true |
| 2025-02 | 1318 | 3 | 0.0023 | true |
| 2025-03 | 1595 | 0 | 0.0000 | true |
| 2025-04 | 1586 | 0 | NULL | false |
| 2025-05 | 1610 | 0 | NULL | false |
| 2025-06 | 1381 | 0 | NULL | false |

Matches the brief's expected values exactly, including 2025-03 reading
`0.0000` (fully observed, genuine zero) rather than NULL.

### Tests

Added `dbt/tests/assert_censored_90d_is_null.sql` and
`dbt/tests/assert_exposed_90d_is_populated.sql`, mirroring the existing
`assert_censored_cohorts_have_null_retention.sql` /
`assert_exposed_cohorts_have_retention.sql` guard pair.

**Inversion proof.** Temporarily flipped the guard in the model's final
`case` expression from `when exp90.has_full_90d_exposure` to
`when not exp90.has_full_90d_exposure`, rebuilt
(`dbt build --select mart_cohort_retention assert_censored_90d_is_null
assert_exposed_90d_is_populated`):

- `assert_censored_90d_is_null` — **FAIL, 33 rows** (the 3 censored
  cohorts x 11 `months_since` rows each = 33).
- `assert_exposed_90d_is_populated` — **FAIL, 99 rows** (the 9 exposed
  cohorts x 11 rows each = 99).

33 + 99 = 132, confirming the inverted guard was exactly backwards
across the whole table, not partially broken. Reverted the single
`case when` line; `git diff` on the model afterward showed no trace of
the inversion (confirmed via `grep -c "not exp90"` returning 0), only
the intended additions.

---

## Task 2 — Parquet export

Added `scripts/export_marts.py` (run via `venv/Scripts/python.exe
scripts/export_marts.py` from the repo root). Opens
`dbt/trading_engine.duckdb` **read-only**, and `COPY ... TO ... (FORMAT
PARQUET)`s each of the six marts plus `dim_date`, `dim_product` and
`dim_campaign` (core-layer views, resolved by temporarily `chdir`-ing
into `dbt/` so their underlying relative CSV reads — `../data/*.csv`,
per `dbt_project.yml`'s `data_dir` var — resolve correctly regardless of
the caller's cwd) to `data/marts/<name>.parquet`. Prints a
name/rows/file-size table and exits non-zero if any export has zero
rows.

Added `data/marts/` to `.gitignore` (build artefacts, regenerated from
the warehouse — not committed, unlike the raw CSVs in `data/`).
`README.md`'s Quickstart section now documents regeneration: `dbt
build`, then `venv/Scripts/python.exe scripts/export_marts.py`.

### Export output

```
name                   |     rows | file size
----------------------------------------------
mart_daily_trading     |     1460 | 58.2 KB
mart_product_daily     |     8760 | 174.0 KB
mart_cohort_retention  |      132 | 3.7 KB
mart_ltv               |      144 | 5.1 KB
mart_email_flow_weekly |      318 | 26.3 KB
mart_data_quality      |     1095 | 5.3 KB
dim_date               |      365 | 4.3 KB
dim_product            |       24 | 3.6 KB
dim_campaign           |       11 | 1.3 KB
```

Exit code 0. All nine files non-zero rows.

---

## Final verification

`dbt/target/` deleted, full `dbt build --profiles-dir .`:

```
Finished running 6 table models, 92 data tests, 14 view models in 22.71s.
Completed with 1 warning (assert_source_date_completeness, 2 rows —
the intentional planted meta_ads_daily gap on 2025-03-15/16).
Done. PASS=111 WARN=1 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=112
```

- Test nodes: 90 -> 92 (+2, matching the 2 tests added; see baseline
  note above re: the brief's stated "93").
- `mart_cohort_retention`: 132 rows (unchanged grain).
- All other mart row counts unchanged: `mart_daily_trading` 1,460;
  `mart_product_daily` 8,760; `mart_ltv` 144; `mart_email_flow_weekly`
  318; `mart_data_quality` 1,095.
- Single WARN is the intentional gap, as required.
- Parquet export re-run clean after the final build, all nine files
  non-zero rows (table above).
