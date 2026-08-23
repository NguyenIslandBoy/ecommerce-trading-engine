# Build Log — Final Fix Wave

Branch: `feat/data-foundation`. This is the last pass before merge, applied
after the whole-branch review of the 15 completed build tasks. Every item
below was verified against a full `dbt build --profiles-dir .` run from a
deleted `dbt/target/` (never a partial-parse cache), and every dollar/row
figure quoted was read back from the built DuckDB warehouse, not assumed.

Baseline measured before any change in this pass: `dbt build` ->
`PASS=106 WARN=1 ERROR=0 TOTAL=107`, **87** test nodes (`dbt list
--resource-type test` and the build log's "N data tests" line agree). The
task brief's stated baseline of "90 test nodes" does not match this
measurement; PASS/WARN/ERROR/TOTAL and every row count in the brief DID
match exactly, so this is treated as a documentation slip in the brief
rather than something broken in this pass — noted here rather than
silently reconciled away.

Commits: `7e27bd2` (Tier A), `dfeb213` (Tier B), plus this doc.

---

## Tier A

### A1 — `mart_product_daily` / `mart_ltv` money reconciliation

**Done.** Added `dbt/tests/assert_mart_money_reconciles.sql`.

- `mart_product_daily`: ties EXACTLY to `fct_order_line` (non-cancelled).
  net_revenue 1,211,689.88, contribution_margin 855,863.08 both sides,
  delta 0.00.
- `mart_ltv`: money is per cohort x channel x horizon, so summing all
  three horizons triple-counts a line that falls inside more than one
  horizon's window. Per the brief, reconciled a single horizon (90, the
  longest).
  - **Checked before committing, as instructed:** horizon=90 summed
    across all cohorts does **not** tie to `fct_order_line`'s full
    non-cancelled total. Delta: net_revenue **-174,701.00**
    (1,036,988.88 vs 1,211,689.88), contribution_margin **-123,418.20**
    (732,444.88 vs 855,863.08). This is real, not a bug: horizon=90 only
    captures revenue within 90 days of each customer's first order, and
    this project's own retention-collapse finding means real revenue
    exists well past that window for the early cohorts (median gap to a
    2nd order is 100 days).
  - Per the brief's instruction to report the delta rather than loosen
    the tolerance, the test does not compare horizon=90 against the full
    `fct_order_line` total. Instead it reconciles against the SAME
    windowed slice of `fct_order_line` the mart is built from
    (join to `dim_customer`, keep lines with
    `order_date in [first_order_date, first_order_date + 90)`,
    non-cancelled), recomputed independently in the test. That ties to
    the penny (1,036,988.88 / 732,444.88, verified) and still catches a
    dropped `not is_cancelled` filter — the actual regression this test
    guards against.
- Tolerance £0.01 throughout, as specified.

### A2 — Channel distribution pin

**Done.** Added `dbt/tests/assert_channel_distribution.sql`. Verified
current shares (non-cancelled orders): google 36.59%, meta 27.55%,
unattributed 26.84%, tiktok 9.01% — matches the brief's quoted 36.5 /
27.5 / 26.9 / 9.0 within rounding. Pinned at ±2pp.

### A3 — Blended margin rate pin

**Done.** Added `dbt/tests/assert_margin_rate_is_sane.sql`, asserting
`sum(contribution_margin)/sum(net_revenue)` (non-cancelled) is
0.706 ± 0.005. Measured value: 0.706338.

### A4 — Spec drift (grain / `n_at_risk`)

**Done.** `docs/specs/2026-08-22-ecommerce-trading-engine-design.md`:

- Sec 4.3: `mart_cohort_retention` grain corrected from
  "cohort × age × as_of" to "cohort × age"; "carries `n_at_risk`"
  corrected to "carries `cohort_size` and ... `has_full_exposure`".
- Sec 5.2, detector 8: "sufficient `n_at_risk`" reworded to "sufficient
  `cohort_size` and `has_full_exposure`".

### A5 — README cost-snapshot documentation

**Done.** Added a "Cost is a snapshot" row to the README's Known Data
Quality Issues table, alongside the existing "Inventory is a snapshot"
row, documenting that `products.csv` carries a single current `cost` per
variant with no history, so COGS applies a present-day cost to
historical orders and the engine cannot see or correct for any cost
change that actually occurred in the period. `fct_order_line.sql`'s
comment referencing "noted as an assumption in the README" is now true
and was left unchanged.

---

## Tier B

### B1 — "Monotonically" claim

**Done**, in both `README.md` and the design spec. Replaced with
"near-monotonically" plus the actual sequence (31.77, 25.17, 15.79, 9.65,
2.41, 0.21, 0.17, 0.23, 0.00) and an explicit call-out that 2025-01 ->
2025-02 ticks up (0.17% -> 0.23%) rather than declining.

### B2 — Partial-day detection

**Done.**

- `mart_data_quality`: added `expected_count`, derived per ad platform as
  the modal (most common) non-zero daily row count — not hardcoded.
  Derived values: google_ads_daily = 5, meta_ads_daily = 6 (matching the
  brief's stated rigid counts, but computed, not literal). Added
  `issue_type = 'partial_day'` where `0 < row_count < expected_count`;
  `missing_day` stays reserved for `row_count = 0`.
- `mart_daily_trading.ad_spend_is_complete`: changed from
  `not bool_or(is_gap)` to `not bool_or(issue_type != 'ok')`, so a
  partial day now also NULLs `blended_cac`, not just a wholly-missing one.
- **Verification (incomplete-day count before/after):** this dataset has
  no genuine partial day today — google is always exactly 5/day and meta
  is always exactly 6/day whenever present at all (confirmed by direct
  query: only two distinct row-count values exist per platform, one of
  them only on the 2 fully-missing meta days). So:
  - Before: 2 incomplete days (2025-03-15, 2025-03-16), both `missing_day`.
  - After: 2 incomplete days, same two dates, same reason.
  - **Zero new days flipped to incomplete** — the extension is inert on
    current data and only activates the day a partial day actually
    occurs, which is exactly the intended behaviour.
  - `blended_cac` confirmed NULL on both gap days (all 4 channel rows
    each date), unchanged from before.

### B3 — `channel_roas` / `channel_margin_roas` VAT documentation

**Done.** Added descriptions to both columns in `_marts.yml`, mirroring
`aov`'s existing note: both are computed on ex-VAT `net_revenue` /
`contribution_margin`, so they read ~17% lower than a VAT-inclusive ROAS
from an ad platform's own dashboard (e.g. Meta Ads Manager) — not a
pipeline error.

### B4 — Backtest trailing-edge documentation

**Done.** Added a row to the README's Known Data Quality Issues table:
ad/email sources sync one day after event date while `dim_date` clamps
to `max(order_date)`, so at any `as_of_date` cursor D, ad spend is
complete only through D-1 — the newest day of every historical rebuild
has NULL `ad_spend`, `channel_cac` and `blended_cac`. Flagged that a
Layer 3 backtest replaying `as_of_date` across ~365 cursors will hit this
every run.

This was independently confirmed while doing the point-in-time
verification below: rebuilding at `as_of_date: 2025-01-31` produces
exactly 2 `missing_day` rows in `mart_data_quality` — but for
2025-01-31 itself on BOTH platforms (the trailing lag), not the March
gap (which sits outside that cursor's spine entirely). This is the
mechanism B4 describes, caught live rather than only in theory.

### B5 — Remove `ltv_horizon_days`

**Done.** Removed from `dbt/dbt_project.yml`'s `vars:` block. Confirmed
no remaining references anywhere in `dbt/` (`grep` across `.sql`/`.yml`,
excluding `target/`).

### B6 — Bound `generate_series` in `mart_cohort_retention`

**Done — derived, not tested.** Replaced the hardcoded
`generate_series(0, 11)` with a bound computed from the data:
`datediff('month', min(cohort_month), max(dim_date.date_day))`. Verified
the derived bound equals 11 on current data (matching the old literal)
and the model's row count is unchanged at **132**.

### B7 — `assert_new_customer_count_agrees` per (month × channel)

**Done.** Extended the test (same file, still one test node) to also
compare `mart_daily_trading.new_customers` against `mart_ltv.cohort_size`
per (month_start × channel) cell, in addition to the pre-existing
three-way total check. Verified: all **48** cells (12 months x 4
channels) agree exactly, zero mismatches, before and after the change —
test passes on the real data as the brief states it should.

### B8 — Extend LTV guard tests to `ltv_revenue`

**Done.** `assert_censored_ltv_is_null` and `assert_exposed_ltv_is_populated`
now check `ltv_revenue` alongside `ltv_margin` (both must be NULL when
censored, both must be populated when exposed). Two lines each, as
scoped.

### B9 — LTV/CAC headline pairing

**Done — named the pairing explicitly** (did not recompute to matched
months, to avoid touching a validated headline number used elsewhere in
the doc). Added a parenthetical to the design spec's "LTV/CAC 4.4x ->
2.1x" line stating it pairs each period's 60-day cohort LTV against
2025-06's blended CAC throughout (current unit economics), and that the
2025-04 cohort read against its own contemporaneous CAC (£12.47) is
2.4x — still a clear compression, just a different number from 2.1x.

---

## Final verification

1. `dbt/target/` deleted, full `dbt build --profiles-dir .` run clean
   from empty cache (done 3 times across this pass: post-Tier-A,
   post-Tier-B, and this final confirmation run).
2. **Final build line:**
   `Done. PASS=109 WARN=1 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=110`
   — exactly one WARN, and it is `assert_source_date_completeness`
   reporting exactly 2 rows (`meta_ads_daily` / 2025-03-15 and
   2025-03-16, both `issue_type='missing_day'`) — the intentional
   planted gap, unchanged from baseline.
   Test node count: **90**, up from the measured baseline of **87** —
   +3, matching the three new singular tests added in Tier A
   (`assert_mart_money_reconciles`, `assert_channel_distribution`,
   `assert_margin_rate_is_sane`). Tier B added no new test nodes: B7 and
   B8 extended existing test files rather than adding new ones, and B2/B6
   are model changes with no accompanying new schema test (kept
   deliberately minimal, per the brief's own scope).
3. **Point-in-time check** at `--vars '{as_of_date: 2025-01-31}'`:
   `mart_daily_trading` `max(date_day) = 2025-01-31`, row count **860**,
   confirmed by direct query. All reconciliation tests
   (`assert_ad_spend_reconciles`, `assert_revenue_reconciles_to_source`,
   `assert_mart_money_reconciles`, `assert_new_customer_count_agrees`)
   passed. Build reported 1 WARN at this cursor too, but for the B4
   trailing-lag reason on 2025-01-31 itself, not the March gap (see B4
   above) — expected, not a regression. Default vars restored and the
   warehouse rebuilt clean afterward (final build line above is from
   that restored-default run).
4. **All six mart row counts confirmed unchanged** at default vars:
   - `fct_order_line`: 42,779
   - `mart_daily_trading`: 1,460
   - `mart_ltv`: 144
   - `mart_product_daily`: 8,760
   - `mart_cohort_retention`: 132
   - `mart_email_flow_weekly`: 318
   Headline figures also unchanged: net revenue (non-cancelled)
   1,211,689.88; contribution margin 855,863.08; blended margin_pct
   0.706338; new customers 20,284; ad spend 247,493.29.

## Not done / deferred

Nothing in Tiers A or B was skipped. The only open item is the
pre-existing test-count discrepancy in the task brief's stated baseline
(90 vs the measured 87), noted above rather than silently resolved,
since it predates this pass and the measured PASS/WARN/TOTAL and every
dollar figure in the brief's "current verified state" otherwise matched
exactly.
