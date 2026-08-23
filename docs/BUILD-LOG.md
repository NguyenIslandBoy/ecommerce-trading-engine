# SDD ledger — plan: docs/plans/2026-08-22-01-data-foundation.md

Spec: docs/specs/2026-08-22-ecommerce-trading-engine-design.md (read, authoritative)
Branch: feat/data-foundation
Branch base: 8572e77

## Setup rulings

Ruling: use a git branch, not a git worktree — the Python venv lives at `venv/` inside
the repo directory and is git-ignored, so a worktree would have no interpreter and every
dbt command in the plan would fail. Cost if wrong: work happens on a branch in the primary
working directory rather than an isolated copy; fully recoverable via git.

## Pre-flight conflict scan

### Cross-task rows (tasks sharing a file or interface)

| Tasks | Produces → consumes | Finding |
|---|---|---|
| T1 → T2..T15 | `dbt_project.yml` vars (`as_of_date`, `data_dir`, `vat_rate`, `ltv_horizon_days`); `_sources.yml` | Clean. All four vars are referenced downstream and all are defined in T1. |
| T2 → T3,T4 | `_staging.yml` created by T2, appended by T3 and T4 | Clean. Sequential appends, no overlapping model entries. |
| T2 → T3,T5,T6,T7,T15 | `stg_orders` | Clean. All consumers use `order_id`/`customer_id`/`order_date`/`channel`/`created_at`, all produced by T2. |
| T2 → T2,T6 | macro `channel_from_referrer` | Clean. Applied once in `stg_orders`; `dim_customer` reads the resulting `channel` column rather than re-deriving it. |
| T2 → T2,T3,T4 | macro `as_of_filter` | Clean. `stg_products` deliberately omits it (no timestamp in the catalogue); `stg_order_lines` inherits availability via its inner join to `stg_orders`. |
| T3 → T6,T7 | `stg_customers`, `stg_products`, `stg_order_lines` | Clean. |
| T3 → T3..T15 | `packages.yml` / dbt_utils | Clean. T3 Step 1 writes dbt_utils tests, Step 2 runs `dbt deps` before `dbt test`. Correct order; a reversed order would fail to parse. |
| T4 → T5,T8 | `stg_ads_daily` | Clean. `dim_campaign` and `fct_ad_spend_daily` both build `campaign_key` as `platform \|\| ':' \|\| campaign_id`; identical expression in both. |
| T4 → T8,T14 | `stg_email_flows` | Clean. Weekly grain preserved through `fct_email_flow_weekly` into `mart_email_flow_weekly`; never joined to a daily fact. |
| T5 → T8,T9,T10,T11 | `dim_date` | Clean. Spine runs min(order_date)=2024-07-01 to as_of=2025-06-30; ad dates span exactly 2024-07-01..2025-06-30, so the T8 relationships test on `ad_date` is satisfiable. Verified against the CSVs. |
| T6 → T7,T12,T13 | `dim_customer` | Clean. |
| T6,T7 | BOTH derive "first order" independently — `dim_customer` via `arg_min(channel, created_at)`, `fct_order` via `min(created_at)` | **Finding F1 — duplicated aggregate.** See ruling below. |
| T7 → T9..T13,T15 | `fct_order`, `fct_order_line` | Clean. Downstream reads `net_revenue` (ex-VAT) and `contribution_margin` only; `*_incl_vat` used solely by T7 Step 6 and T15 reconciliation. |
| T8 → T9,T10 | `fct_ad_spend_daily` | Clean. |
| T9 → T10..T14 | `_marts.yml` created by T9, appended by T10-T14 | Clean. Sequential appends. |
| T10 → T15 | `mart_daily_trading.net_revenue` | Clean. T15 divides the VAT-inclusive source side by `1 + vat_rate` to compare like with like. |

### Per-task self-consistency rows

| Task | Self-consistent? |
|---|---|
| T1 | Yes. Step 7 tests the `{name}` placeholder and carries an inline fallback. |
| T2 | Yes. Step 2's expected failure is a dbt parse error naming the missing model — accurately stated. Step 7 proves the as_of filter bites by comparing two var values. |
| T3 | Yes. Step 6a asserts ex-VAT margins 0.640/0.820 and names the VAT-inclusive figures as the failure signature. |
| T4 | Yes. Step 6 asserts Meta 363 days vs Google 365 — the planted gap must survive staging for T9 to detect it. |
| T5 | Yes. DuckDB-version fallback supplied for `unnest(generate_series(...))`. |
| T6 | Yes. |
| T7 | Yes. Step 6 reconciliation confirmed against the CSVs pre-flight (zero variance); Step 6a asserts VAT was removed. |
| T8 | Yes. Step 6 asserts the CPC = CPM / (1000 × CTR) identity holds. |
| T9 | Yes — deliberately expects a FAILING test (2 rows) as its deliverable, then downgrades to `warn`. Reviewers must not treat that expected failure as a defect. |
| T10 | Yes. Step 5 reproduces the spec's profiled CAC figures from the mart. Note: `dim_date` is joined twice (once inside `grid`, once at the end) — redundant, harmless. |
| T11 | Yes. `days_of_cover` applies a current inventory snapshot historically — stated in the model comment. See F3. |
| T12 | Yes. DuckDB-version fallback supplied for `to_months`. Step 5 shows the artifact and the guard side by side. |
| T13 | Yes. |
| T14 | Yes. Rates are recomputed from summed numerators, not averaged from message-level rates — stated in the model comment. |
| T15 | Yes. Guardrail test's threshold (email join matching >20% of orders) is calibrated to the verified 86% mismatch. Verified: zero blank order emails, so the NULL-safety of the join holds. |

### Pre-flight rulings

Ruling: F1 — accept the duplicated first-order aggregate in `dim_customer` and `fct_order`.
The two need different projections (channel via `arg_min` vs. a row-level timestamp match),
so a shared model would serve two consumers badly. Verified pre-flight that **no customer has
two non-cancelled orders sharing a `created_at`**, so `fct_order.is_first_order` cannot flag
two rows and the definitions cannot disagree. T10 Step 5, which reproduces known CAC figures,
is the standing net. Cost if wrong: a future edit to one definition diverges silently from the
other; T10 Step 5 would catch it on the next full build.

Ruling: F2 — accept the redundant second `dim_date` join in T10. It costs one extra join on a
365-row table and keeps the `grid`/enrichment CTEs readable. Cost if wrong: negligible.

Ruling: F3 — accept historical `days_of_cover` computed from a current inventory snapshot.
The catalogue supplies no inventory history, so no alternative exists. The metric is meaningful
only at the final as_of date and says so in the model comment. Cost if wrong: a backtest in
Plan 3 that reads `days_of_cover` at a historical date gets a misleading value — Plan 3 must
read inventory signals only at the run's as_of date.

Ruling: T9's intentionally-failing completeness test is correct behaviour, not a defect. Every
task reviewer for T9 will be told this explicitly in its constraints block. Cost if wrong: a
reviewer flags it, I adjudicate, one wasted round.

## Task progress

Task 1: implementer DONE (commit 359ad19). `{name}` placeholder in meta.external_location
rendered correctly on first attempt — no per-table fallback needed. `dbt debug` → All checks
passed. All seven sources return their expected row counts (orders 26553 verified).

Ruling: two untracked files left by Task 1 are folded into Task 2's dispatch rather than
fixed in the controller session (controller fixes skip review):
  - `dbt/.user.yml` — dbt telemetry file, must be gitignored or it pollutes every later
    review package's git status.
  - `data_engineer_task_brief.md` / `.pdf` — the provided task brief; belongs in the repo
    as an input document, so Task 2 commits it.
Cost if wrong: trivial; both are one-line changes reversible at any point.

### Pre-verified targets (checked against CSVs before dispatch)

| Task | Check | Verified expected value |
|---|---|---|
| T2 | stg_orders at as_of 2024-12-31 | **14741** (brief said "roughly 14,300" — WRONG, corrected in dispatch) |
| T3 | stg_products margin_pct min/max | 0.640 / 0.820 |
| T4 | stg_ads_daily rows/days | meta 2178 rows / 363 days; google 1825 rows / 365 days |
| T5 | dim_date / dim_campaign rows | 365 / 11 |
| T6 | customers / blank_email / marketable | 20817 / 623 / **9071** (see Ruling T6 below) |
| T7 | line net incl-VAT / ex-VAT / margin_pct | 1453925.32 / 1211604.43 / 0.706 |
| T10 | mart_daily_trading rows | 1460 (365 x 4 channels) |
| T11 | mart_product_daily rows | 8760 (365 x 24 variants) |
| T14 | Welcome Series open / click / conv | 0.516->0.402, 0.118->0.090, conv FLAT 0.0337->0.0323 |
| T15 | mart rows at as_of 2025-01-31 | 860 (215 days x 4) |

Ruling: T4 plan defect — the `stg_ads_daily` model comment claims "google_ads_daily carries
device and ad_network_type, so a campaign-day appears on multiple rows". FALSE: google has
1825 rows and 1825 distinct campaign-days, i.e. exactly 5 campaigns x 365 days with one
device/network label decorating each row. The GROUP BY rollup is therefore a no-op. Decision:
KEEP the rollup (it makes the declared grain explicit and gives the uniqueness test something
real to assert) but the implementer must correct the comment to state the truth. Device and
network are dropped; no detector uses them. Cost if wrong: a redundant GROUP BY on 4k rows.

Ruling: T6 plan defect — Step 6 expects "marketable strictly less than 9071 because subscribed
customers with blank emails are excluded". FALSE: all 623 blank-email customers are already
`not_subscribed`, so marketable == subscribed == **9071** exactly. Decision: correct the
expected value to 9071 in the T6 dispatch. Cost if wrong: the implementer chases a
non-existent bug, or worse "fixes" correct code to force an impossible inequality.

Task 2: implementer DONE (commit c2df0d1). 1 view built, 6/6 tests pass. as_of proof:
26553 at default vs 14741 at 2024-12-31 — matches the corrected target exactly.

Ruling: SYSTEMIC dbt gotcha #1 — partial parsing. After a "write the failing test, run it,
watch it fail" step, dbt leaves a stale `target/partial_parse.msgpack` whose manifest omits
the new tests, so the NEXT build silently runs the model with 0 tests discovered. Task 2 hit
this and fixed it by deleting `dbt/target/`. Decision: every subsequent dispatch (T3-T15) must
instruct the implementer to delete `dbt/target/` (or pass `--no-partial-parse`) between the
expected-failure run and the build step, AND to confirm the test count in the build output is
non-zero. Cost if wrong: a task appears green having run no tests at all — the worst possible
failure mode for this plan, since the tests ARE the deliverable.

Ruling: SYSTEMIC dbt gotcha #2 — `dbt show --vars` does not rebuild upstream views. Staging
models are views whose `var("as_of_date")` is baked into the CREATE VIEW text at build time,
so querying via `ref()` with a different `--vars` returns the ORIGINAL build's rows. Any
point-in-time verification must `dbt run --vars ... --select <model>` first, then query, then
rebuild with default vars to leave committed state consistent. Task 2 discovered and worked
around this correctly. Affects T15 Step 4 (which already builds first, so it is safe as
written) and any ad-hoc as_of check. Cost if wrong: a false "point-in-time correctness
verified" claim — which is the single most important architectural property of this build.

Task 2: review returned 2 Important, BOTH plan-mandated. Controller rulings:

Ruling: T2 finding 1 (deprecated generic-test syntax) — CONFIRMED REAL, FIX IT, and propagate
plan-wide. Verified empirically: the plan's `tests:` + top-level `values:` emits 7
`MissingArgumentsPropertyInGenericTestDeprecation` warnings per run; rewriting as `data_tests:`
with `values` nested under `arguments:` gives 0 warnings and the same 6/6 PASS. The plan
specifies the deprecated form in EIGHT more tasks (T3,T4,T5,T6,T8,T10,T13,T14), so fixing the
pattern now avoids eight repeats and a deprecation that becomes an error in dbt 2.0. All
subsequent dispatches must specify the corrected syntax. Cost if wrong: none identified —
verified working before ruling.

Ruling: T2 finding 2 (`channel_from_referrer` puts categorical business logic in a
"cast/rename-only" staging model) — ACCEPT the code, AMEND the constraint. The reviewer is
correct that a LIKE-based taxonomy is not a cast or a rename. But `channel` is a deterministic
single-column decode with no joins or aggregation, consumed by nearly every downstream model;
deriving it once in staging is right, and relocating it now would ripple through T3-T15. The
honest fix is to make the constraint match the intent: staging permits deterministic
single-column decoding, but no joins, no aggregation, no cross-source rules. Spec assumption
to be updated at Task 15. Cost if wrong: an architectural purist disagrees; zero functional
impact, and the logic stays in one macro either way.

Ruling: T2 minor (repeated `lower(coalesce(...))` per CASE arm) — DEFERRED. DuckDB evaluates
it once per row per arm at negligible cost on 26k rows, and restructuring the macro to hoist it
would hurt readability. Not fixed.

Task 2: fix round 1/5 (1 addressed, 1 no-change-by-ruling, 1 minor deferred; commits
c2df0d1..0df35a9). Deprecation warnings 7 -> 0, PASS=6 retained.
Plan corrected and committed (a90434b): 60 `tests:`->`data_tests:`, 32 arg blocks nested under
`arguments:`, plus the T6 marketable=9071 and T4 no-fan-out fixes. Briefs 3-15 regenerated
from the corrected plan.

Task 2: complete (commits 359ad19..0df35a9, review clean after 1 fix round).
Task 3: implementer DONE (commit 127dfee). 3 models, **14 data tests ran**, all PASS.
stg_order_lines=42779, stg_customers=20817 (623 blank email), stg_products=24,
margin_pct 0.640-0.820 ex-VAT. dbt_utils tests confirmed working with `arguments:` nesting —
the convention is now settled for all remaining tasks.

Ruling: MAJOR CORRECTION to the spec's centerpiece claim. The spec asserted the cohort
retention collapse was a right-censoring ARTIFACT and that the ~24% monthly repeat rate proved
retention held. Verified against the CSVs — that is WRONG:
  - 90-day repeat rate by cohort, ALL with full 90-day exposure: 31.8% (2024-07), 25.2%
    (2024-08), 15.8% (2024-09), 9.7% (2024-10), 2.4% (2024-11), 0.2% (2024-12), 0.17%
    (2025-01), 0.23% (2025-02), 0.00% (2025-03). Monotonic, fully observed, REAL.
  - Censoring is real but explains ONLY cohorts 2025-04 onward at 90 days.
  - The ~24% monthly repeat rate is real but MASKS the collapse: it is carried by the
    Jul-Nov 2024 cohorts. In 2025-06 the latest cohort contributing any repeat order is
    2025-02 — essentially no 2025-acquired customer ever returns.
  - Commercial consequence: 60-day contribution-margin LTV £42.80 -> £30.24 (-29%) while
    blended CAC rose £9.68 -> £14.84 (+53%). LTV/CAC 4.4x -> 2.1x.
Decision: the trap is INVERTED from what the spec said — the blended metric looks healthy
while cohort quality collapsed. Corrected spec sections 2.2, 2.3, 5.2, 5.4, build sequence;
corrected plan Task 12 (intro, model comment, Steps 5/6, commit message) and the README block
in Task 15. Regenerated briefs 12, 13, 15. The censoring guard REMAINS correct and necessary —
its purpose is now stated as separating observed zeros from unobserved ones, not explaining
the decline away. Cost if wrong: this is the headline finding of the whole submission; if the
per-cohort figures were miscomputed the narrative inverts again. They were computed twice by
different routes (grid + left join, and a 90-day window per customer) with matching results.

Task 3: complete (commits a90434b..127dfee, review clean — Approved, 1 Important plan-mandated
resolved by spec amendment below, 2 minors deferred).

Ruling: staging-purity constraint AMENDED at spec level (raised independently by both the T2
and T3 reviewers, so it would have recurred on T4). The original wording "casting and renaming
only; no business logic" is genuinely violated by `channel` (LIKE taxonomy), `has_valid_email`
(derived boolean) and `margin_pct` (VAT-adjusted ratio). Rather than relocate three derivations
into every downstream consumer, or pretend the constraint was met, the constraint now states
the real boundary: staging may cast, rename, normalise, and derive deterministically from a
single source column; it may NOT join across sources or aggregate. Spec section 4.1 and the
plan's file-structure row updated. Cost if wrong: an architectural reviewer prefers a stricter
staging layer; zero functional impact, and every derivation stays in exactly one place.

Task 3: minor (deferred): `price / (1 + vat_rate)` computed three times in stg_products, and
the rounded `price_ex_vat` DECIMAL(12,2) is not the same intermediate used inside `margin_pct`,
so reconciling `(price_ex_vat - unit_cost)/price_ex_vat` against `margin_pct` downstream can
show sub-penny drift. Verbatim from the brief. No consumer reconciles those two, so deferred.
Task 3: minor (deferred): dbt warns "Configuration paths exist ... which do not apply to any
resources" for models.core and models.marts — self-resolving once Tasks 5-14 add those models.

Task 4: implementer DONE_WITH_CONCERNS (commit 01e150e) — 2 models, 8 tests PASS, but row/day
counts fell short of targets. Implementer correctly refused to alter vars/macros to force a
match and escalated instead.

Ruling: T4 escalation was RIGHT — plan defect, not code defect. Verified against the CSVs:
meta/google/email all carry `_weld_synced` = event_date + 1 day; orders sync same-day. With
the default cursor at 2025-06-30, the `as_of_filter` correctly excluded the final day of every
lagged source (meta 2172/362, google 1820/364, email 624/52). The macro was doing its job; the
DEFAULT CURSOR was wrong. Decision: default `as_of_date` -> **2025-07-01**, i.e. the engine
runs the day after the period closes. That is the natural operating pattern and the first
moment the complete 12 months is actually available; bitemporal correctness is fully preserved
because earlier backtest cursors still exclude rows unsynced at that time. Verified at
2025-07-01: meta 2178/363, google 1825/365, email 636/53 — all targets restored, and
stg_orders is unchanged at 26553 because orders have zero lag.
Consequence handled: `dim_date`'s spine must clamp to `least(as_of_date, max(order_date))` or
it gains a trailing empty day and every mart built on it inherits one. Plan Task 5 updated,
briefs 1 and 5 regenerated. Cost if wrong: if a reviewer considers running the engine one day
after period close to be sidestepping the lag rather than modelling it, the alternative is to
accept NULL ad spend on the final day of every mart — which would also break Task 10's CAC
verification targets.

Task 4: fix round 1/5 (1 addressed; commits 01e150e..b45ca83). All targets restored:
meta 2178/363/conv=0, google 1825/365/conv=1825, email 636/53, stg_orders 26553 unchanged.
28 data tests PASS across the full build.

Post-cursor-change re-verification (confirms the 2025-07-01 ruling broke nothing downstream):
  - dim_date spine = 365 days; EVERY day has orders, so mart_data_quality produces no
    spurious "missing day" rows for the orders source.
  - meta = exactly 2 gap days, google = 0. T9's "test must fail with exactly 2 rows"
    expectation still holds.
  - T10 CAC targets unchanged: meta 19.82 (2024-07) -> 34.74 (2025-06);
    google 11.73 -> 15.26.

Task 4: review Approved with 2 Important, both plan-mandated. Controller rulings:

Ruling: T4 finding 1 (brief self-contradiction) — FIXED IN PLAN, no code change. The brief's
Interfaces line declared `status` for stg_email_flows while its own Step 4 SQL emits
`message_channel`/`flow_status`/`message_status`. The SQL was right; the prose was wrong.
Verified `fct_email_flow_weekly` (Task 8) already reads `flow_status`, so nothing downstream
depended on the wrong name. Plan line 599 corrected. Cost if wrong: none, doc-only.

Ruling: T4 finding 2 (no test guards the NULL-vs-zero invariant) — VALID, FIXING. The reviewer
is right that the task's headline requirement rested only on a one-off manual query. Added
`assert_meta_reports_no_conversions.sql` guarding BOTH directions (Meta must be NULL, Google
must not be), plus a mandatory mutation check: flip the NULL to 0, confirm the test fails with
2178 rows, revert. A test that cannot fail is not a test. Added to the plan as Task 4 Step 6a
so it is part of the permanent record, not just this run. Cost if wrong: one extra test file;
the mutation check makes a false-green impossible.

Task 4: minor (deferred): `sum()` over DECIMAL(12,2) widens precision in DuckDB, so the
materialised column type may not match the declared DECIMAL(12,2) interface even though values
are exact at this data volume.
Task 4: minor (deferred): `cost_micros / 1000000.0` uses a double intermediate before the
decimal cast; negligible at micros scale.

Task 4: fix round 2/5 (2 addressed, 0 open; commits b45ca83..a11cf71). Mutation check verified
credible by re-reviewer: PASS -> FAIL 2178 rows -> PASS, revert byte-identical, 29 tests green.
Task 4: complete (commits 31e3f4c..a11cf71, review clean after 2 fix rounds).

Task 5: implementer DONE (commit 2c3712d). dim_date=365 rows (2024-07-01..2025-06-30) — the
clamp works; dim_campaign=11 rows, zero 'other'. 6 data tests PASS. Primary
`unnest(generate_series(...))` form worked, no fallback needed.

Ruling: SYSTEMIC dbt gotcha #3 — `dbt show` truncates to 5 rows by default. Task 5's
funnel_stage group-by returned 7 groups but displayed 5, which could have hidden a campaign
falling through to 'other'. Added to standing-dispatch-context.md: always pass an explicit
`--limit` on verification queries and state expected-vs-seen row counts. Cost if wrong: a
verification silently checks only part of its result set — exactly the false-green class of
failure this plan is most exposed to.

Task 5: complete (commits bbbe4cd..2c3712d, review clean — Approved, no Critical/Important).
Reviewer independently re-derived all 11 campaign names from the raw CSVs and traced each
through the funnel_stage CASE rather than trusting the report; confirmed zero 'other'.
Task 5: minor (deferred): funnel_stage LIKE-prefix ordering rationale lives only in the SQL
comment, not in _core.yml. No ambiguity in current data ('brand%' cannot match 'non-brand...'
under prefix matching).

Task 6: implementer DONE (commit acfee17). 2 views, 7 data tests PASS. customers=20817,
blank_email=623, marketable=9071 — all exact on first build, confirming the corrected
expectation. NULL first_order_date = 533 (cancelled-only customers), matching prediction.

Task 6: complete (commits 2c3712d..acfee17, review clean — Approved, no Critical/Important).
Reviewer independently counted 7 tests from the diff YAML and confirmed the partial-parse
cache was cleared before the run.
Task 6: minor (deferred): `is_marketable` is not defensively coalesced — if a future refresh
had a NULL `email_consent_state` on a valid-email customer, the expression yields NULL and the
not_null test would fail rather than resolving to FALSE. Not a live defect (test passes on real
data). FINAL REVIEW: consider `coalesce(email_consent_state,'') = 'subscribed'`.
Task 6: plan doc fixed (not a code defect): dim_customer's "Produces" interface omitted
`accepts_marketing` while the Step 3 SQL selected it. Interface line corrected.

Task 7: implementer DONE (commit 85c8cc9). 9 new tests; FULL project rebuild 51 data tests PASS.
mismatched_orders=0, margin_pct=0.706, rows 26553/42779, incl_vat=1453925.32 exact.
Implementer flagged ex_vat 1211689.88 vs my stated 1211604.43 and correctly diagnosed per-line
rounding rather than forcing a match.

Ruling: T7 ex_vat variance — implementer is RIGHT, my target was computed the wrong way.
Diagnosed precisely: 31,514 lines round UP vs 9,542 DOWN, net +£85.94 (0.007% of £1.21m). The
bias is systematic, not noise, because nearly every price ends in .99 and X.99/1.2 lands just
below a half-penny boundary (24.99/1.2=20.825 -> 20.83). Per-line rounding to pence is the
CORRECT behaviour: it is what an invoice and a tax authority see. Decision: accept per-line
rounding, correct the plan's stated expectation, and do NOT chase equality with incl_vat/1.2.
margin_pct — the metric anything downstream actually consumes — is exact at 0.706.

Ruling: DOWNSTREAM BREAK caught by the above. Task 15's reconciliation test compared the mart's
ex-VAT total against source/1.2 with a £1.00 tolerance; the £86 rounding gap would have FAILED
it, and the naive fix (widen the tolerance) would have blinded the test to real fan-out bugs
too. Rewrote it as TWO EXACT hops instead: source -> fct_order_line on the untransformed
VAT-inclusive value (exact, no arithmetic applied yet), then fct_order_line -> mart on ex-VAT
net_revenue (exact, the mart only sums what the fact computed). Tolerance tightened from £1.00
to £0.01 on both hops. Reconcile what should be identical; never widen a tolerance to absorb a
known-lossy transformation. Cost if wrong: none identified — strictly stronger than the
original single lossy comparison.

Task 7: complete (commits acfee17..85c8cc9, review clean — Approved, no Critical/Important).
Reviewer independently reconstructed the full 9-new / 51-data-test / 63-node count from the
YAML files rather than trusting the report, verified operator precedence on the VAT expression
(`/` binds tighter than `-`, so cogs is correctly NOT divided), traced every money column's
type through the whole lineage to rule out a DOUBLE leak, and checked the 3-valued-logic edge
case where a customer's orders are ALL cancelled (`false AND NULL` = `false`, so no nulls leak
into is_first_order).
Task 7: minor (deferred): the ex-VAT expression is written out twice in fct_order_line rather
than computed once; verbatim from the brief. Readability only.

Task 8: implementer DONE (commit 15bc711). 5 data tests PASS. fct_ad_spend_daily=4003 rows
(meta 2178/363 dates incl. the planted gap, google 1825/365); fct_email_flow_weekly=636/53;
CPC identity broken=0; Meta NULL invariant intact. All exact on first build.
CORE LAYER COMPLETE (dim_date, dim_campaign, dim_customer, dim_product, fct_order,
fct_order_line, fct_ad_spend_daily, fct_email_flow_weekly).

Task 8: complete (commits 19110b9..15bc711, review clean — Approved, no Critical/Important).
Reviewer verified the CPC identity ALGEBRAICALLY rather than trusting broken=0:
cpm/(1000*ctr) = (1000*spend/impr)/(1000*clicks/impr) = spend/clicks = cpc. Also confirmed all
six derived-rate columns use nullif(denominator,0) guards, and that _core.yml was a true append
with the six pre-existing entries untouched.

Task 8: minor (deferred) — FLAG FOR FINAL REVIEW: the CPC identity is only an ad-hoc
`dbt show` check, not a persisted test. This is the same pattern as the Task 4 NULL-invariant
finding, which I did act on. Stakes here are high: the CPC decomposition (splitting Meta's cost
rise into a CPM/auction component and a CTR/creative component) is the headline commercial
analysis of the whole engine, and a silent regression in cpm/ctr would invert the
recommendation. Not fixed now only because the identity holds by construction and cannot drift
without someone editing the SQL. Recommend adding `dbt_utils.expression_is_true` at final
review. Cost if wrong: a future formula edit goes undetected.
Task 8: minor (deferred): only `open_rate` has an accepted_range bound; click/conversion/
unsubscribe rates have none. Verbatim from the brief.
Task 8: minor (deferred): fct_email_flow_weekly ships message_name/flow_status/total_orders/
unsubscribe_rate which the brief's abstract interface list omits (the brief's own Step 4 SQL
includes them). Interface doc vs shipped columns diverge.
Task 8: minor (deferred): the report claimed `--limit` was passed on verification queries but
the shown commands do not demonstrate it. Moot — every query was an aggregate returning <=2
rows — but the evidence did not establish the claim.

Task 9: implementer DONE (commit 2f4dae5). THE SHOWPIECE WORKED: assert_source_date_completeness
failed with EXACTLY 2 rows (meta_ads_daily 2025-03-15 and 2025-03-16), zero gaps for orders and
google, then downgraded to severity=warn giving a final state of WARN 2 (not PASS, not ERROR).
mart_data_quality = 1095 rows (365 x 3 sources). Full build: 59 data tests, 74 nodes,
PASS=73 WARN=1 ERROR=0. The planted defect is now detected mechanically on every build.

Task 9: complete (commits 15bc711..2f4dae5, review clean — Approved, no Critical/Important).
Reviewer specifically verified the JOIN DIRECTION — the one thing that would have silently
made this whole task useless (joining the other way yields zero gap rows and a passing test).
Spine is correctly on the left in all three CTEs. Also confirmed severity='warn' is in the
final committed state and that no backfill logic was added.
Task 9: minor (deferred): mart_data_quality scans fct_ad_spend_daily twice (once per platform)
rather than one pass with conditional aggregation. Verbatim from the brief; cheap at this
volume.

Task 10: implementer DONE (commit 9549288). MILESTONE: all four CAC targets reproduced EXACTLY
through 10 models of pipeline — meta 19.82 (2024-07) -> 34.74 (2025-06), google 11.73 -> 15.26.
These were computed independently from the raw CSVs before any model existed, so this validates
the whole chain: channel mapping, is_first_order, the as_of cursor, and the spend join.
Row count 1460 (365x4) exact. tiktok/unattributed both days_with_spend=0 and days_with_cac=0,
confirming NULL-not-zero for the uncostable channels. 5 tests (implementer confirmed they were
real by observing a genuine "model not found" failure first). Full build: 80 tests,
PASS=79 WARN=1 (the Task 9 data-quality warning).
Noted: meta shows 363/365 spend-days, which is the planted gap correctly propagating.

Task 10: complete (commits 2f4dae5..9549288, review clean — Approved, no Critical/Important).
Reviewer surfaced a subtle and important design point worth recording: the NULL-CAC guarantee
for TikTok holds specifically BECAUSE ad_spend/clicks/impressions are the only fact columns
NOT coalesced to 0 in the final select. TikTok has real orders (nonzero new_customers), so had
ad_spend been coalesced to 0, channel_cac would compute 0/N = 0 and falsely claim free customer
acquisition for 9% of the business. The un-coalesced NULL is load-bearing, not an oversight.
Reviewer also independently re-derived the CAC arithmetic on 6 of the 24 rows
(7836.90/668=11.73, 13026.63/375=34.74) rather than trusting the table.

Ruling: PLAN WORDING FIXED across 12 occurrences. Every task's "write the failing test" step
said `Expected: FAIL — model not found`, but dbt's actual behaviour varies: sometimes a
compilation error, sometimes `WARNING: Did not find matching node for patch` + "Nothing to do"
(NO-OP). Task 10's implementer hit the NO-OP form and had to judge whether its step had
succeeded. Reworded to describe both outcomes and to state the real criterion — the test must
not report PASS. Briefs 11-15 regenerated. Cost if wrong: none; strictly clearer.
Task 10: minor (deferred): no inline `config(materialized='table')` in the mart SQL; relies on
the folder-level default in dbt_project.yml. Verified correct in the build log, but drift-prone.

Task 11: implementer DONE (commit 1696240). 3 tests PASS, row count 8760 (365x24) exact.
D3 breakout confirmed: 219 (2024-07) -> 701 (2025-03), holding 668/652/621 — the planted
product signal is visible in the mart. VIT-D3 SKUs show days_of_cover 33.5 and 44.1 vs
47.6-213 for the rest of the catalogue, so the stockout signal is separable.
Implementer found another plan defect and worked around it correctly rather than reporting a
false result.

Ruling: T11 finding — EXPOSURE/DATE COMPARISONS MUST USE THE LAST DATE WITH DATA, NOT THE RUN
CURSOR. Knock-on from the 2025-07-01 cursor ruling. T11's Step 5b filtered
`date_day = var('as_of_date')` = 2025-07-01, but dim_date clamps to 2025-06-30, so the query
returned 0 rows. Worse, the same class of comparison appears in T12's `has_full_exposure`
(2 sites) and T13's LTV exposure guard (1 site) — models NOT YET BUILT. There, comparing a
window_end to a cursor one day past period close would credit a cohort with a day of
observation that does not exist, shifting the censoring boundary by a day. Fixed all 4 sites to
`(select max(date_day) from dim_date)` and documented the reasoning in the T12 model comment.
Briefs 11-13 regenerated. Verified the month-granularity censoring boundary is unmoved
(cohort 2025-04 at ms=3 remains the first censored one either way); only the day-granularity
LTV guard was actually at risk. Cost if wrong: none identified — comparing to the last date
with data is unambiguously the correct basis for "has this window been observed".

Task 11: complete (commits 9549288..1696240, review clean — Approved, no Critical/Important).
Reviewer verified ALL FIVE window functions are partitioned by variant_id and ordered by
date_day — an unpartitioned frame would average velocity across all 24 products and be
silently, plausibly wrong. Also confirmed the dense grid + coalesce(0) is present so zero-sale
days produce rows and the trailing means cannot skip days.

Ruling: T11 minor ACTED ON — mart_product_daily's comment claimed the inventory-snapshot
limitation was "flagged in the README", but nothing documented it. Added a Known-Issues row to
Task 15's README content, including the Plan 3 consequence: a backtest must read inventory
signals at the run date, never historically. Brief 15 regenerated and verified.
Task 11: minor (deferred): velocity_28d computed three times, velocity_7d twice, and
dim_product joined a second time though `grid` already cross-joined it. All verbatim from the
brief; correctness unaffected.
Task 11: minor (deferred): `product_type` selected but absent from the brief's interface list.

INCIDENT (controller error, corrected): commit 5316ebf landed with a message describing the
README change above, but actually contained only editor-applied markdown table reformatting —
my exact-string anchor had failed on the changed whitespace, and `git add -A docs/` swept up
the formatter's churn instead. Amended to `fcb610a` with an accurate "style:" message noting
the mislabelling, then applied the real README change separately.
LESSON: the user's editor auto-formats docs/plans/*.md table whitespace. Use whitespace-tolerant
REGEX for edits to that file, never exact-string anchors, and never `git add -A docs/` without
first checking `git diff --stat` for unrelated formatter churn.

Task 12: implementer DONE (commit 23c6f4a). THE CENSORING GUARD WORKS. 1 model + 4 tests PASS.
132 rows, exposure split exactly 66 TRUE / 66 FALSE.
assert_censored_cohorts_have_null_retention PASSED.
The demonstration pair at months_since=3 came out exactly right:
  cohort 2024-10 -> raw 0.0000, has_full_exposure TRUE,  retention_rate 0.0   (observed zero)
  cohort 2025-04 -> raw 0.0000, has_full_exposure FALSE, retention_rate NULL  (unobserved)
Same number, opposite meaning, correctly distinguished. Step 5 (24 rows) and Step 6 (12 rows)
both matched expectations on the first build.

Ruling: SYSTEMIC dbt gotcha #4 — `dbt show`'s table renderer truncates float DISPLAY WIDTH
independently of the row limit (0.1219 renders as 0.121...). The gotcha #3 `--limit` fix does
NOT address this. Task 12's implementer caught it and cross-checked with `--output json`.
Added to standing-dispatch-context.md. Cost if wrong: a verifier reads a truncated decimal as
the true value — a silent wrong answer rather than a visible error, which is the exact failure
class this plan is most exposed to.

Task 12: review Approved with 1 Important (plan-mandated) + 4 minors. Controller rulings:

Ruling: T12 finding 1 (YAML description contradicts the model) — VALID, FIXING. The
_marts.yml description said retention_rate is NULL "as of var('as_of_date')", which is exactly
what the model deliberately does NOT do (it uses max(dim_date.date_day)). This is the
maintainer-facing text in dbt docs, and it misstates the single most consequential design
decision in the model — a maintainer changing the cursor expecting the boundary to move would
be wrong. Inherited verbatim from my brief template, so the plan is fixed too. Cost if wrong:
none, doc-only, and leaving it would actively mislead.

Ruling: T12 minor (one-directional guard test) — ELEVATED AND FIXED, not deferred. The
reviewer noted assert_censored_cohorts_have_null_retention only checks censored=>NULL, so a
guard inverted to always-NULL would pass while erasing every real signal (cohort 2024-10's
legitimate 0.0 would silently vanish). Given the guard direction IS this model's entire
purpose, one-directional coverage is not adequate. Added
assert_exposed_cohorts_have_retention.sql for the converse, with a mandatory inversion check
(expect 66 rows failing — the fully-exposed half). Same discipline as the Task 4 NULL
invariant. Cost if wrong: one extra test file.

Task 12: minor (deferred): `ages` hardcodes generate_series(0,11) tied to the ~12-month window,
with no comment; silently wrong if the data window grows.
Task 12: minor (deferred): `window_end` is exposed as an output column but undocumented in
_marts.yml.
Task 12: minor (deferred): the .sql header uses the bare phrase "as_of_date" generically before
clarifying it does not mean var('as_of_date') — same terminology overload that caused finding 1.

Task 12: fix round 1/5 (2 addressed, 0 open; commits 23c6f4a..135c98e). Re-reviewer confirmed
model logic untouched (mart_cohort_retention.sql absent from the diff) and the inversion check
credible: with the guard inverted, the ORIGINAL censoring test still PASSED while the new
converse test FAILED with exactly 66 rows — the blind spot demonstrated, not asserted.
Task 12: complete (commits 39b8f89..135c98e, review clean after 1 fix round).

Task 13: implementer DONE_WITH_CONCERNS (commit 209bdaf). 1 model + 3 tests PASS. 144 rows
exact. Censoring boundary exact at horizon=60 (2025-05 and 2025-06 censored across all four
channels, 40 TRUE / 8 FALSE). ltv_revenue > ltv_margin on all 144 rows. Three of four spot
values matched exactly; cohort sizes matched on all four.

Ruling: T13 1-cent variance (meta 2024-07 = 44.21 vs my stated 44.20) — IMPLEMENTER IS RIGHT,
my brief figure was wrong. Verified: summing UNROUNDED per-line margins gives 44.2031 -> 44.20
(my original method); summing the per-line-ROUNDED contribution_margin that fct_order_line
actually stores gives 44.206 -> 44.21. The model's value is correct and consistent with the
warehouse's own per-line rounding, which was already adjudicated as correct behaviour in Task 7
(it is what an invoice shows). This is the THIRD manifestation of that same root cause
(ex_vat total, now LTV) — my golden figures computed in pandas/duckdb outside the warehouse
will differ in the last cent from figures computed through fct_order_line. Note for Plan 3:
any expected-value fixture must be computed THROUGH the warehouse, not alongside it.
Cost if wrong: sub-penny; margin_pct and all cohort sizes unaffected.

Ruling: T14 PLAN DEFECT found in pre-verification, fixed BEFORE dispatch. The source's
`Flow_ID` is NOT a flow identifier — it is a MESSAGE identifier. There are 12 Flow_IDs across
only 6 real flows ("Welcome Series" = FL001+FL002+FL003), with exactly one Message_ID per
Flow_ID. My Task 14 grouped by flow_id, which would have produced 12 x 53 = 636 rows — the
same message grain fct_email_flow_weekly already has, i.e. no rollup at all — and
"Welcome Series open rate" would have become three separate per-message series. Any
engagement-decay detector reading it would compare a message against itself rather than
tracking the flow. Regrained to flow_name: 6 x 53 = **318** rows, verified against the CSV.
Added a GRAIN WARNING comment to the model so the trap is documented in code, and a failure
signature to the build step (636 rows means you grouped on the wrong key). Brief 14
regenerated. Cost if wrong: none — flow_name is the only real flow identity available, and
318 is confirmed correct.

Task 14: implementer DONE (commit 9439d00). 1 model + 3 tests PASS. Row count **318** — the
flow_name regrain is confirmed correct (636 would have meant the flow_id trap). Welcome Series
open_rate 0.515 -> 0.401 (-22%) with conv_rate 0.0336 -> 0.0323 (flat, -4%): the
engagement-down/monetisation-intact signature the artifact classifier depends on.
Implementer correctly noted my Step 5 verification query averages weekly rates unweighted
(display only) which is why it differs trivially from my monthly-recomputed 0.516/0.0337; the
mart rows themselves are properly recomputed from summed numerators. Accurate distinction —
ironically the same averaging flaw the model comment warns against, present in my check query
rather than in the model.

Task 13: review Approved with 1 Important (plan-mandated) + 2 minors.

Ruling: T13 finding — SAME DEFECT AS T12, PROPAGATED FROM MY BRIEF TEMPLATE. mart_ltv's
_marts.yml description said has_full_exposure is false "where the horizon extends past
var('as_of_date')", while the SQL correctly uses max(dim_date.date_day). Identical wording
error to the one the T12 reviewer caught in mart_cohort_retention — my template carried it into
both censoring-aware marts. Fixed the plan and routed the code fix to the T13 implementer.
This is now the SECOND instance, so the root cause is the template, not either task: any future
censoring-aware model must state the max-date basis explicitly. Cost if wrong: none, doc-only,
but leaving it would mislead a maintainer on the column feeding the CAC/LTV judgement.
Task 13: minor (deferred): stray blank line after `spend_within as (`.
Task 13: minor (deferred): nullif(cohort_size,0) is unreachable — cohort_size comes from
count(*) and can never be 0.

Task 14: complete (commits 209bdaf..9439d00, review clean — Approved, no Critical/Important).
Reviewer independently confirmed the implementer's rate-discrepancy explanation was CORRECT:
the model recomputes rates from summed numerators at (flow_name, week_start); the 0.515-vs-0.516
gap originates in MY Step 5 display query, which averages weekly rates unweighted across a
month. Model is sound; my check query was the flawed one.
Resolved the reviewer's one open question (integer-division risk on revenue_per_recipient):
stg_email_flows casts order_value to DECIMAL(12,2) and recipients to BIGINT, and DuckDB's
DECIMAL/BIGINT yields DECIMAL — no truncation possible. Closed, not a defect.
Task 14: minor (deferred): unique_opens/clicks/unsubscribes/orders are carried into the final
select via `select *` but absent from the brief's declared interface list. Verbatim from brief.

Task 13: fix round 1/5 (1 addressed, 0 open; commits 209bdaf..da43af8). mart_ltv.sql confirmed
unchanged (git diff empty before commit); only _marts.yml staged. 3 tests + 1 model PASS.
Task 13: complete (commits efe8963..da43af8, review clean after 1 fix round).

Task 15: implementer DONE_WITH_CONCERNS (commit a2e1fcc). Full build: 100 nodes (6 table +
14 view models + 80 data tests), PASS=99 WARN=1 ERROR=0 — the sole WARN is
assert_source_date_completeness "Got 2 results" (the planted Meta gap), exactly as designed.
Both reconciliation hops PASS (hop1 diff 3.5e-07, hop2 diff 0.00). POINT-IN-TIME PROOF:
rebuild at as_of_date 2025-01-31 gave max(date_day)=2025-01-31 and n=860 rows exactly
(215 days x 4 channels), then default vars restored and re-verified.
Raised three concerns, ALL THREE VALID.

Ruling: T15 concern 1 (Important) — REAL DESIGN DEFECT IN MY TEST, FIXING. hop1_source reads
the raw CSVs with no availability filter while fct_order_line IS as_of-filtered, so the test
fails by construction at ANY historical cursor. This is not cosmetic: Plan 3 backtests the
engine by rebuilding across ~365 cursors, and a reconciliation test that errors at every one
would train its operator to ignore reconciliation failures entirely. Fixed by mirroring
`as_of_filter('o._weld_synced','o.created_at')` onto the source side so both sides always
describe the same population. Implementer must now prove it PASSES at as_of 2025-01-31.
Cost if wrong: none — filtering both sides identically is strictly more correct.

Ruling: T15 concern 2 (Minor, user-facing) — README quickstart is broken. `pip` is absent
because the venv was created by `uv`, so `venv/Scripts/pip install` fails for anyone following
the README. Replaced with `uv pip install --python venv/Scripts/python.exe`, plus a stdlib
fallback line. Cost if wrong: none.

Ruling: T15 concern 3 (Minor, genuinely confusing) — two different metrics both called
"retention". The README's 31.8% -> 0.0% is a ROLLING 90-DAY repeat rate; mart_cohort_retention
.retention_rate at months_since=3 is CALENDAR-MONTH-BUCKETED and reads 13.89% for the same
2024-07 cohort. Both correct, names collide, and a reviewer comparing them would think the
README contradicts the warehouse. Requiring an explicit disambiguating clause. The substantive
claim (decline is real, not censoring) is unchanged. Cost if wrong: none.

Task 15: fix round 1/5 (3 addressed, 0 open; commits a2e1fcc..f15d356).
assert_revenue_reconciles_to_source now PASSES at as_of 2025-01-31 (was ERROR) AND at default.
README quickstart fixed to uv (verified: venv has no pip.exe; uv 0.12.3 on PATH; syntax checked
against uv --help by the reviewer). Retention metrics disambiguated.
Task 15: complete (commits da43af8..f15d356, review clean after 1 fix round).

=== ALL 15 TASKS COMPLETE ===
Final state: 100 nodes (20 models: 6 staging, 8 core, 6 marts + 80 data tests).
PASS=99 WARN=1 ERROR=0. Sole WARN is the intentional planted-gap detector.
Point-in-time proven: rebuild at 2025-01-31 -> 860 rows (215 days x 4 channels).
Both reconciliation hops pass at default AND historical cursors.
Branch feat/data-foundation: 35 commits from base 8572e77.


=== FINAL WHOLE-BRANCH REVIEW (opus) — Ready with caveats ===
Reviewer independently re-derived every headline number from the built warehouse and confirmed
cross-mart consistency (orders 25720, new_customers 20284 agreeing three ways, ad spend
247493.29 identical across staging/core/mart, mart_ltv cohort sizes summing to
mart_cohort_retention). Then found what 15 per-task reviews structurally could not.

Controller independently verified the three correctness findings before routing fixes:
  1. blended_cac on the 2 Meta gap days: 11.52 -> 6.75 / 6.33 -> 11.08. CONFIRMED. Missing
     Meta spend is implicitly coerced to zero, fabricating a 42% CAC improvement on exactly
     the two days the dataset's DQ showpiece is about. Same NULL-vs-zero error the branch
     handles impeccably for Meta conversions, applied in the opposite direction.
  2. contribution_margin <> net_revenue - cogs on 2442 of 42779 lines, drift -24.42.
     CONFIRMED. Root cause: the ex-VAT expression is duplicated and evaluated in DOUBLE, so
     the two columns round independently. T3 and T7 each deferred half of this; neither could
     see that together they publish an inconsistent identity.
  3. mart_ltv publishes ltv_margin for censored cohorts with no guard and no test: 2025-05
     = 31.82, 2025-06 = 31.60, both ABOVE the last fully-exposed cohort (31.01). CONFIRMED.
     An unfiltered detector reads LTV as RECOVERING — the inverse of the headline finding.

Ruling: reviewer's pushback on my T12->T13 propagation is CORRECT and I accept it. I
propagated the description fix to mart_ltv but not the converse-test fix, even though my own
stated rationale ("the guard direction IS this model's entire purpose") applies verbatim.
mart_ltv ended up weaker than pre-fix mart_cohort_retention. Fixing now.
Ruling: reviewer's pushback on F1 is CORRECT. I cited "T10 Step 5 reproduces known CAC
figures" as the standing net for the duplicated first-order aggregate, but that is a manual
step in a plan document and does not survive the plan. Converting to a real test.
Ruling: reviewer's pushback on F3 is CORRECT. I documented the inventory-snapshot half of the
days_of_cover caveat but not the partial-trailing-window half at series start (2024-07-01
velocity_28d = 7.0 from a single day). Same column, same consumer, undocumented.


=== FIX WAVE RE-REVIEW: all 15 addressed, no new breakage ===
Re-reviewer verified against a live rebuilt warehouse: blended_cac NULL on the 2 gap days and
untouched on 363 complete days (guard works both directions, no over-nulling);
contribution_margin identity violations 2442 -> 0 with margin now DERIVED from the rounded
net_revenue rather than recomputed; both mart_ltv guard tests confirmed genuinely opposite
(24 censored / 120 exposed); assert_vat_is_removed tolerance 0.001 against a live ratio of
1.199915 — tight enough to catch a dropped divisor (ratio 1.0, off by 0.2), loose enough for
per-line rounding. Intentional WARN preserved. Row counts unchanged (42779 / 144 / 1460).

Ruling: PARKED residual — the two mart_ltv guard tests assert `ltv_margin` only, not
`ltv_revenue`. Live data confirms `ltv_revenue` IS correctly guarded (same
`case when has_full_exposure` construct produces both), so this is a test-coverage gap rather
than a defect. Parked rather than fixed because: (a) both columns are guarded by one shared
expression, so a regression isolated to `ltv_revenue` alone would require someone editing one
branch of a two-column CASE; (b) `ltv_margin` is the column the CAC/LTV detector consumes —
`ltv_revenue` exists for contrast; (c) the process allows one fix wave, not two. Recommend
extending both tests to cover `ltv_revenue` at the start of Plan 2. Cost if wrong: a
single-column regression in a secondary metric ships undetected.

=== BRANCH COMPLETE ===
39 commits from base 8572e77. Final: PASS=106 WARN=1 ERROR=0 TOTAL=107, 87 data tests,
20 models. Point-in-time verified at both default and historical cursors.
