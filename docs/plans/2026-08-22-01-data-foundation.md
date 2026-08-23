# Data Foundation Implementation Plan (Plan 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform seven raw CSVs into a tested, point-in-time-correct dbt/DuckDB warehouse whose marts are the only surface the detection layer will read.

**Architecture:** dbt-duckdb reads the CSVs as external sources. Staging views cast and rename with no business logic. A core star schema carries COGS and contribution margin. Marts expose a "metric spine" at fixed grains. Every model is filtered by an `as_of_date` var applied to row *availability* (`_weld_synced`), not event time, so the warehouse can be rebuilt as of any historical date.

**Tech Stack:** dbt-core 1.12.3, dbt-duckdb 1.11.0, DuckDB 1.5.5, Python 3.12.4.

**Spec:** `docs/specs/2026-08-22-ecommerce-trading-engine-design.md`

## Global Constraints

- Python is `venv/Scripts/python.exe`; dbt is `venv/Scripts/dbt.exe`. Never invoke a bare `python` or `dbt`.
- All dbt commands run from the `dbt/` directory. Use `--project-dir dbt --profiles-dir dbt` from repo root if preferred, but be consistent.
- Money columns are `DECIMAL(12,2)`. Never leave money as `DOUBLE`.
- Source prices are VAT-inclusive (20%, `taxes_included` true on every order) but `products.cost` is ex-VAT. `net_revenue` and `margin_pct` divide by `1 + var('vat_rate')` before COGS is subtracted. Taking margin on the VAT-inclusive figure overstates it by 6 to 8 points per variant and corrupts every downstream LTV and ROAS.
- Customers join on `customer_id` only. Email is never a join or dedup key (86% of orders carry a different email domain than their customer record).
- Cancelled orders are excluded from all revenue, margin and retention metrics. They remain visible in `fct_order` with `is_cancelled = true`.
- `stg_ads_daily.platform_conversions` is `NULL` for Meta, never `0`.
- The `as_of_date` var defaults to `2025-06-30` and is threaded through every staging model via the `as_of_filter` macro.
- All models are `view` except marts, which are `table`.
- Commit after every task. Commit messages use Conventional Commits (`feat:`, `test:`, `chore:`).

---

## File Structure

| Path                                         | Responsibility                                                                                                             |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `data/*.csv`                               | The seven provided raw files, moved from repo root                                                                         |
| `dbt/dbt_project.yml`                      | Project config, vars (`as_of_date`, `data_dir`), materialisations                                                      |
| `dbt/profiles.yml`                         | DuckDB connection to`dbt/trading_engine.duckdb`                                                                          |
| `dbt/macros/as_of_filter.sql`              | Availability filter shared by all staging models                                                                           |
| `dbt/macros/channel_from_referrer.sql`     | Referrer → channel mapping, used in staging and tests                                                                     |
| `dbt/models/staging/_sources.yml`          | External CSV source definitions                                                                                            |
| `dbt/models/staging/stg_*.sql`             | One view per source; casts, renames, and single-column deterministic derivations. No joins across sources, no aggregation. |
| `dbt/models/staging/_staging.yml`          | Column docs and schema tests for staging                                                                                   |
| `dbt/models/core/dim_*.sql`, `fct_*.sql` | Star schema                                                                                                                |
| `dbt/models/core/_core.yml`                | Keys, relationships, accepted values                                                                                       |
| `dbt/models/marts/mart_*.sql`              | The metric spine                                                                                                           |
| `dbt/models/marts/_marts.yml`              | Mart tests                                                                                                                 |
| `dbt/tests/*.sql`                          | Singular tests (completeness, reconciliation, margin sanity)                                                               |

---

## Task 1: Project scaffold and DuckDB connection

**Files:**

- Create: `data/` (move 7 CSVs into it)
- Create: `dbt/dbt_project.yml`, `dbt/profiles.yml`, `dbt/models/staging/_sources.yml`
- Modify: `.gitignore`

**Interfaces:**

- Consumes: nothing.
- Produces: dbt project rooted at `dbt/`, source `{{ source('raw', <table>) }}` for the seven tables, vars `as_of_date` and `data_dir`.

- [ ] **Step 1: Move the CSVs into `data/`**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task"
mkdir -p data
git mv customers.csv email_flows.csv google_ads_daily.csv meta_ads_daily.csv order_lines.csv orders.csv products.csv data/ 2>/dev/null || mv customers.csv email_flows.csv google_ads_daily.csv meta_ads_daily.csv order_lines.csv orders.csv products.csv data/
ls data/
```

Expected: seven `.csv` files listed.

- [ ] **Step 2: Create `dbt/dbt_project.yml`**

```yaml
name: trading_engine
version: 1.0.0
config-version: 2
profile: trading_engine

model-paths: ["models"]
macro-paths: ["macros"]
test-paths: ["tests"]
target-path: "target"
clean-targets: ["target", "dbt_packages"]

vars:
  # Point-in-time cursor: the date the engine RUNS, not the last date of data.
  #
  # Set to 2025-07-01 (the day after the period closes) deliberately. Ad and
  # email rows carry a 1-day ingestion lag: _weld_synced for the 2025-06-30
  # event date is 2025-07-01T00:00. Orders sync same-day. So an engine running
  # ON 2025-06-30 genuinely cannot see 2025-06-30's ad spend, and every mart's
  # final day would show orders with NULL spend and NULL CAC.
  #
  # Running the day after the period closes is both the natural operating
  # pattern and the first moment the complete 12 months is actually available.
  # Point-in-time correctness is fully preserved — backtests at earlier
  # cursors still exclude rows that had not synced by then.
  as_of_date: '2025-07-01'
  # Path to the raw CSVs, relative to the dbt project directory.
  data_dir: '../data'
  # Horizon used for CAC comparison in the LTV mart.
  ltv_horizon_days: 60
  # UK VAT. Source prices are VAT-inclusive; product costs are not.
  vat_rate: 0.20

models:
  trading_engine:
    staging:
      +materialized: view
      +schema: staging
    core:
      +materialized: view
      +schema: core
    marts:
      +materialized: table
      +schema: marts
```

- [ ] **Step 3: Create `dbt/profiles.yml`**

```yaml
trading_engine:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: trading_engine.duckdb
      threads: 4
      extensions:
        - httpfs
```

- [ ] **Step 4: Create `dbt/models/staging/_sources.yml`**

```yaml
version: 2

sources:
  - name: raw
    description: "Provided CSV extracts. Read in place by DuckDB; never copied into the warehouse."
    meta:
      external_location: "read_csv_auto('{{ var('data_dir') }}/{name}.csv')"
    tables:
      - name: orders
        description: "Order-level data, Shopify schema. 26,553 rows."
      - name: order_lines
        description: "Line-item detail per order. 42,779 rows."
      - name: customers
        description: "Customer profiles and marketing consent. 20,817 rows."
      - name: products
        description: "Product catalogue at variant grain. 24 rows."
      - name: meta_ads_daily
        description: "Meta Ads daily campaign performance. 2,178 rows. Reports NO conversions."
      - name: google_ads_daily
        description: "Google Ads daily campaign performance. 1,825 rows."
      - name: email_flows
        description: "Klaviyo flow engagement, WEEKLY grain (53 run dates). 636 rows."
```

- [ ] **Step 5: Add dbt artefacts to `.gitignore`**

Append these lines to `.gitignore` (some may already be present; do not duplicate):

```
dbt/trading_engine.duckdb
dbt/target/
dbt/logs/
dbt/dbt_packages/
```

- [ ] **Step 6: Verify the connection**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe debug --profiles-dir .
```

Expected: `All checks passed!`

- [ ] **Step 7: Verify the external source placeholder actually renders**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe show --profiles-dir . --inline "select count(*) as n from {{ source('raw','orders') }}"
```

Expected: `n = 26553`.

If this fails with a file-not-found on a literal `{name}`, the placeholder did not render. Fall back to per-table locations — replace the `meta:` block on the source with one `meta.external_location` per table, e.g.:

```yaml
      - name: orders
        meta:
          external_location: "read_csv_auto('../data/orders.csv')"
```

- [ ] **Step 8: Commit**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task"
git add -A
git commit -m "chore: scaffold dbt project and move CSVs into data/"
```

---

## Task 2: Shared macros and `stg_orders`

**Files:**

- Create: `dbt/macros/as_of_filter.sql`, `dbt/macros/channel_from_referrer.sql`
- Create: `dbt/models/staging/stg_orders.sql`, `dbt/models/staging/_staging.yml`

**Interfaces:**

- Consumes: `source('raw','orders')`.
- Produces:
  - Macro `as_of_filter(sync_col, event_col)` → SQL boolean expression.
  - Macro `channel_from_referrer(col)` → SQL expression yielding one of `meta`, `google`, `tiktok`, `unattributed`.
  - Model `stg_orders` with columns: `order_id BIGINT`, `order_name`, `customer_id BIGINT`, `order_email`, `currency`, `total_price DECIMAL(12,2)`, `subtotal_price`, `total_discounts`, `total_line_items_price`, `total_tax`, `total_shipping`, `financial_status`, `fulfillment_status`, `created_at TIMESTAMP`, `order_date DATE`, `cancelled_at TIMESTAMP`, `is_cancelled BOOLEAN`, `cancel_reason`, `buyer_accepts_marketing BOOLEAN`, `source_name`, `referring_site`, `landing_site`, `country_code`, `city`, `order_number BIGINT`, `channel VARCHAR`, `synced_at TIMESTAMP`.

- [ ] **Step 1: Write the failing test**

Create `dbt/models/staging/_staging.yml`:

```yaml
version: 2

models:
  - name: stg_orders
    description: "Orders, cast and renamed. Filtered to rows available as of var('as_of_date')."
    columns:
      - name: order_id
        description: "Shopify order id. Primary key."
        data_tests: [unique, not_null]
      - name: customer_id
        description: "FK to stg_customers. Never null in this dataset."
        data_tests: [not_null]
      - name: order_date
        data_tests: [not_null]
      - name: channel
        description: "Last-click channel derived from referring_site."
        data_tests:
          - accepted_values:
              arguments:
                values: ['meta', 'google', 'tiktok', 'unattributed']
      - name: is_cancelled
        data_tests: [not_null]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe test --profiles-dir . --select stg_orders
```

Expected: FAIL — `Model 'model.trading_engine.stg_orders' not found` (dbt cannot compile a test against a model that does not exist).

- [ ] **Step 3: Create `dbt/macros/as_of_filter.sql`**

```sql
{#
  Point-in-time availability filter.

  A row is visible only once it has been ingested. `_weld_synced` is the
  ingestion timestamp and is the correct basis; where it is absent
  (customers.csv is blank throughout, and DuckDB infers it as VARCHAR)
  we fall back to the row's own event timestamp.

  try_cast is required: a blank string is not castable to timestamp.
#}
{% macro as_of_filter(sync_col, event_col) %}
    cast(
        coalesce(
            try_cast({{ sync_col }} as timestamp),
            cast({{ event_col }} as timestamp)
        ) as date
    ) <= cast('{{ var("as_of_date") }}' as date)
{% endmacro %}
```

- [ ] **Step 4: Create `dbt/macros/channel_from_referrer.sql`**

```sql
{#
  Last-click channel from the Shopify referring_site.

  This is the only attribution available: landing_site carries no UTM
  parameters anywhere in the dataset. Blank referrers and the literal
  string 'direct' both map to 'unattributed' (26.9% of orders combined),
  which is why channel-attributed metrics are confidence-discounted
  downstream.
#}
{% macro channel_from_referrer(col) %}
    case
        when lower(coalesce({{ col }}, '')) like '%facebook%'  then 'meta'
        when lower(coalesce({{ col }}, '')) like '%instagram%' then 'meta'
        when lower(coalesce({{ col }}, '')) like '%google%'    then 'google'
        when lower(coalesce({{ col }}, '')) like '%youtube%'   then 'google'
        when lower(coalesce({{ col }}, '')) like '%tiktok%'    then 'tiktok'
        else 'unattributed'
    end
{% endmacro %}
```

- [ ] **Step 5: Create `dbt/models/staging/stg_orders.sql`**

```sql
with src as (
    select * from {{ source('raw', 'orders') }}
)

select
    id                                                   as order_id,
    name                                                 as order_name,
    customer_id,
    lower(trim(email))                                   as order_email,
    currency,
    cast(total_price as decimal(12,2))                   as total_price,
    cast(subtotal_price as decimal(12,2))                as subtotal_price,
    cast(total_discounts as decimal(12,2))               as total_discounts,
    cast(total_line_items_price as decimal(12,2))        as total_line_items_price,
    cast(total_tax as decimal(12,2))                     as total_tax,
    cast(total_shipping_price_set_shop_money_amount
         as decimal(12,2))                               as total_shipping,
    financial_status,
    fulfillment_status,
    cast(created_at as timestamp)                        as created_at,
    cast(created_at as date)                             as order_date,
    cast(cancelled_at as timestamp)                      as cancelled_at,
    cancelled_at is not null                             as is_cancelled,
    cancel_reason,
    buyer_accepts_marketing,
    source_name,
    referring_site,
    landing_site,
    shipping_address_country_code                        as country_code,
    shipping_address_city                                as city,
    order_number,
    {{ channel_from_referrer('referring_site') }}        as channel,
    try_cast(_weld_synced as timestamp)                  as synced_at

from src
where {{ as_of_filter('_weld_synced', 'created_at') }}
```

- [ ] **Step 6: Build and test**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe build --profiles-dir . --select stg_orders
```

Expected: 1 model built, 5 tests PASS.

- [ ] **Step 7: Verify the as_of filter actually bites**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe show --profiles-dir . --inline "select count(*) as n from {{ ref('stg_orders') }}"
../venv/Scripts/dbt.exe show --profiles-dir . --vars '{as_of_date: 2024-12-31}' --inline "select count(*) as n from {{ ref('stg_orders') }}"
```

Expected: first returns 26553; second returns a materially smaller number (roughly 14,300). If both return 26553 the filter is not wired — stop and fix before continuing.

- [ ] **Step 8: Commit**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task"
git add -A
git commit -m "feat: add as_of and channel macros with stg_orders"
```

---

## Task 3: `stg_order_lines`, `stg_customers`, `stg_products`

**Files:**

- Create: `dbt/models/staging/stg_order_lines.sql`, `stg_customers.sql`, `stg_products.sql`
- Modify: `dbt/models/staging/_staging.yml`

**Interfaces:**

- Consumes: `as_of_filter`, `stg_orders` (for the order-line availability join).
- Produces:
  - `stg_order_lines`: `order_line_id`, `order_id`, `line_index`, `sku`, `product_title`, `variant_title`, `vendor`, `product_id`, `variant_id`, `quantity BIGINT`, `unit_price DECIMAL(12,2)`, `total_discount DECIMAL(12,2)`, `grams`, `fulfillment_status`.
  - `stg_customers`: `customer_id`, `customer_email`, `has_valid_email BOOLEAN`, `source_order_count BIGINT`, `source_total_spent DECIMAL(12,2)`, `state`, `accepts_marketing BOOLEAN`, `email_consent_state`, `created_at TIMESTAMP`, `signup_date DATE`.
  - `stg_products`: `variant_id` (PK), `product_id`, `product_title`, `product_type`, `sku`, `variant_title`, `price DECIMAL(12,2)`, `compare_at_price DECIMAL(12,2)`, `unit_cost DECIMAL(12,2)`, `margin_pct DECIMAL(6,4)`, `weight_grams`, `inventory_quantity BIGINT`, `status`, `vendor`.

- [ ] **Step 1: Write the failing tests**

Append to `dbt/models/staging/_staging.yml`:

```yaml
  - name: stg_order_lines
    description: "Order line items. Availability inherited from the parent order."
    columns:
      - name: order_line_id
        data_tests: [unique, not_null]
      - name: order_id
        data_tests:
          - not_null
          - relationships:
              arguments:
                to: ref('stg_orders')
                field: order_id
      - name: variant_id
        data_tests:
          - relationships:
              arguments:
                to: ref('stg_products')
                field: variant_id
      - name: quantity
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 1
                inclusive: true

  - name: stg_customers
    description: "Customer profiles. NOTE source_order_count includes cancelled orders."
    columns:
      - name: customer_id
        data_tests: [unique, not_null]
      - name: has_valid_email
        description: "False for the 623 customers with a blank email string."
        data_tests: [not_null]
      - name: email_consent_state
        data_tests:
          - accepted_values:
              arguments:
                values: ['subscribed', 'not_subscribed']

  - name: stg_products
    description: "Product catalogue at variant grain."
    columns:
      - name: variant_id
        data_tests: [unique, not_null]
      - name: unit_cost
        data_tests: [not_null]
      - name: margin_pct
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
                max_value: 1
```

This introduces `dbt_utils`. Create `dbt/packages.yml`:

```yaml
packages:
  - package: dbt-labs/dbt_utils
    version: [">=1.1.0", "<2.0.0"]
```

- [ ] **Step 2: Install the package and run the test to verify it fails**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe deps --profiles-dir .
../venv/Scripts/dbt.exe test --profiles-dir . --select stg_order_lines stg_customers stg_products
```

Expected: the test does NOT pass. dbt will do one of two things depending on how the
reference resolves: raise a compilation error naming the missing model, or emit
`WARNING: Did not find matching node for patch` and report "Nothing to do" (NO-OP).
Either outcome confirms the test cannot pass before the model exists. What matters is that
it does not report PASS — do not treat a NO-OP as a failure of this step.

- [ ] **Step 3: Create `dbt/models/staging/stg_order_lines.sql`**

`index` is a DuckDB keyword and must be quoted.

```sql
with src as (
    select * from {{ source('raw', 'order_lines') }}
)

select
    src.id                                          as order_line_id,
    src.order_id,
    src."index"                                     as line_index,
    src.sku,
    src.title                                       as product_title,
    src.variant_title,
    src.vendor,
    src.product_id,
    src.variant_id,
    cast(src.quantity as bigint)                    as quantity,
    cast(src.price as decimal(12,2))                as unit_price,
    cast(src.total_discount as decimal(12,2))       as total_discount,
    src.grams,
    src.fulfillment_status

from src
-- Availability is inherited from the parent order: a line cannot be visible
-- before its order is. This keeps the as_of cursor consistent across the join.
inner join {{ ref('stg_orders') }} o
    on o.order_id = src.order_id
```

- [ ] **Step 4: Create `dbt/models/staging/stg_customers.sql`**

`last_name` is inferred as BIGINT by DuckDB (values are numeric strings) and is not used downstream, so it is dropped rather than cast.

```sql
with src as (
    select * from {{ source('raw', 'customers') }}
)

select
    id                                              as customer_id,
    nullif(lower(trim(email)), '')                  as customer_email,
    nullif(trim(email), '') is not null             as has_valid_email,
    -- Source-provided aggregate. Counts CANCELLED orders, so it will not
    -- match a derived count; the discrepancy is surfaced in mart_data_quality.
    cast(order_count as bigint)                     as source_order_count,
    cast(total_spent as decimal(12,2))              as source_total_spent,
    state,
    accepts_marketing,
    email_marketing_consent_state                   as email_consent_state,
    cast(created_at as timestamp)                   as created_at,
    cast(created_at as date)                        as signup_date

from src
where {{ as_of_filter('_weld_synced', 'created_at') }}
```

- [ ] **Step 5: Create `dbt/models/staging/stg_products.sql`**

The catalogue has no timestamp, so it is not as_of filtered — it is treated as a slowly-changing reference table available from the start.

```sql
with src as (
    select * from {{ source('raw', 'products') }}
)

select
    variant_id,
    product_id,
    product_title,
    product_type,
    sku,
    variant_title,
    cast(price as decimal(12,2))                        as price,
    cast(price / (1 + {{ var('vat_rate') }})
         as decimal(12,2))                              as price_ex_vat,
    cast(compare_at_price as decimal(12,2))             as compare_at_price,
    cast(cost as decimal(12,2))                         as unit_cost,
    -- Margin on the EX-VAT price. Source prices include 20% VAT; cost
    -- does not. Using the inclusive price here would read 71.7% for
    -- CBD Oil 10ml against its true 66.0%.
    cast((price / (1 + {{ var('vat_rate') }}) - cost)
         / nullif(price / (1 + {{ var('vat_rate') }}), 0)
         as decimal(6,4))                               as margin_pct,
    weight_grams,
    cast(inventory_quantity as bigint)                  as inventory_quantity,
    status,
    vendor

from src
```

- [ ] **Step 6: Build and test**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe build --profiles-dir . --select stg_order_lines stg_customers stg_products
```

Expected: 3 models built, all tests PASS.

- [ ] **Step 6a: Verify margins are ex-VAT**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe show --profiles-dir . --inline "
select round(min(margin_pct),3) as min_margin, round(max(margin_pct),3) as max_margin
from {{ ref('stg_products') }}"
```

Expected: `min_margin = 0.640`, `max_margin = 0.820`.

If you see `0.717` / `0.850` the VAT divisor is missing — those are the VAT-inclusive figures and every downstream margin will be overstated.

- [ ] **Step 7: Commit**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task"
git add -A
git commit -m "feat: add order line, customer and product staging models"
```

---

## Task 4: `stg_ads_daily` and `stg_email_flows`

**Files:**

- Create: `dbt/models/staging/stg_ads_daily.sql`, `dbt/models/staging/stg_email_flows.sql`
- Modify: `dbt/models/staging/_staging.yml`

**Interfaces:**

- Produces:
  - `stg_ads_daily`: `platform VARCHAR`, `campaign_id`, `campaign_name`, `account_name`, `ad_date DATE`, `impressions BIGINT`, `clicks BIGINT`, `spend DECIMAL(12,2)`, `reach BIGINT`, `frequency DOUBLE`, `platform_conversions DOUBLE` (NULL for Meta), `platform_conversion_value DECIMAL(12,2)` (NULL for Meta), `synced_at`.
  - `stg_email_flows`: `flow_id`, `flow_name`, `message_id`, `message_name`, `message_channel`, `week_start DATE`, `flow_status`, `message_status`, `recipients BIGINT`, `unique_opens BIGINT`, `unique_clicks BIGINT`, `unique_unsubscribes BIGINT`, `unique_orders BIGINT`, `total_orders BIGINT`, `order_value DECIMAL(12,2)`, `tags`, `synced_at`.

- [ ] **Step 1: Write the failing tests**

Append to `dbt/models/staging/_staging.yml`:

```yaml
  - name: stg_ads_daily
    description: "Meta and Google daily spend unioned to one grain. Meta reports NO conversions."
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [platform, campaign_id, ad_date]
    columns:
      - name: platform
        data_tests:
          - accepted_values:
              arguments:
                values: ['meta', 'google']
      - name: spend
        data_tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
                inclusive: true
      - name: ad_date
        data_tests: [not_null]

  - name: stg_email_flows
    description: "Klaviyo flow engagement. WEEKLY grain - do not join to daily facts without dim_date.week_start."
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [flow_id, message_id, week_start]
    columns:
      - name: recipients
        data_tests: [not_null]
      - name: week_start
        data_tests: [not_null]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe test --profiles-dir . --select stg_ads_daily stg_email_flows
```

Expected: the test does NOT pass. dbt will do one of two things depending on how the
reference resolves: raise a compilation error naming the missing model, or emit
`WARNING: Did not find matching node for patch` and report "Nothing to do" (NO-OP).
Either outcome confirms the test cannot pass before the model exists. What matters is that
it does not report PASS — do not treat a NO-OP as a failure of this step.

- [ ] **Step 3: Create `dbt/models/staging/stg_ads_daily.sql`**

```sql
{#
  Unions the two ad platforms onto one grain.

  Meta reports impressions, clicks and spend but NO conversions. Those
  columns are NULL rather than 0: coercing them to zero would make Meta's
  ROAS read as 0.0 instead of "unknown", which silently corrupts any
  cross-platform efficiency comparison.

  Google reports cost in micros (1e6 = one currency unit) and does report
  conversions, but on a different attribution basis than Shopify - it runs
  a stable ~2.0x the Shopify-attributed order count.
#}

with meta as (

    select
        'meta'                                          as platform,
        campaign_id,
        campaign_name,
        account_name,
        cast(date as date)                              as ad_date,
        cast(impressions as bigint)                     as impressions,
        cast(clicks as bigint)                          as clicks,
        cast(spend as decimal(12,2))                    as spend,
        cast(reach as bigint)                           as reach,
        cast(frequency as double)                       as frequency,
        cast(null as double)                            as platform_conversions,
        cast(null as decimal(12,2))                     as platform_conversion_value,
        try_cast(_weld_synced as timestamp)             as synced_at

    from {{ source('raw', 'meta_ads_daily') }}
    where {{ as_of_filter('_weld_synced', 'date') }}

),

google as (

    select
        'google'                                        as platform,
        campaign_id,
        campaign_name,
        account_descriptive_name                        as account_name,
        cast(date as date)                              as ad_date,
        cast(impressions as bigint)                     as impressions,
        cast(clicks as bigint)                          as clicks,
        cast(cost_micros / 1000000.0 as decimal(12,2))  as spend,
        cast(null as bigint)                            as reach,
        cast(null as double)                            as frequency,
        cast(conversions as double)                     as platform_conversions,
        cast(conversions_value as decimal(12,2))        as platform_conversion_value,
        try_cast(_weld_synced as timestamp)             as synced_at

    from {{ source('raw', 'google_ads_daily') }}
    where {{ as_of_filter('_weld_synced', 'date') }}

),

unioned as (
    select * from meta
    union all
    select * from google
)

-- Roll up to the declared grain. google_ads_daily carries device and
-- ad_network_type columns, but they do NOT fan out the grain: the file has
-- 1825 rows and 1825 distinct campaign-days (5 campaigns x 365 days), each
-- row simply tagged with one device/network label. This GROUP BY is
-- therefore a no-op on the current data. It is kept deliberately so the
-- declared grain is enforced in code rather than assumed, which is what
-- gives the uniqueness test something real to assert. Device and network
-- are dropped; no detector uses them.
select
    platform,
    campaign_id,
    max(campaign_name)                  as campaign_name,
    max(account_name)                   as account_name,
    ad_date,
    sum(impressions)                    as impressions,
    sum(clicks)                         as clicks,
    sum(spend)                          as spend,
    sum(reach)                          as reach,
    avg(frequency)                      as frequency,
    sum(platform_conversions)           as platform_conversions,
    sum(platform_conversion_value)      as platform_conversion_value,
    max(synced_at)                      as synced_at

from unioned
group by platform, campaign_id, ad_date
```

Note: `sum()` over an all-NULL group returns NULL in DuckDB, so Meta's conversion columns stay NULL after the rollup. Verify this in step 6.

- [ ] **Step 4: Create `dbt/models/staging/stg_email_flows.sql`**

```sql
{#
  Klaviyo flow engagement. Run_Date is a WEEKLY snapshot (53 distinct
  dates across the 12 months), not a daily one. Every downstream consumer
  must respect that grain.
#}

with src as (
    select * from {{ source('raw', 'email_flows') }}
)

select
    Flow_ID                                         as flow_id,
    Flow_Name                                       as flow_name,
    Message_ID                                      as message_id,
    Message_Name                                    as message_name,
    Message_Channel                                 as message_channel,
    cast(Run_Date as date)                          as week_start,
    Status                                          as flow_status,
    Message_Status                                  as message_status,
    cast(Total_Recipients as bigint)                as recipients,
    cast(Unique_Opens as bigint)                    as unique_opens,
    cast(Unique_Clicks as bigint)                   as unique_clicks,
    cast(Unique_Unsubscribes as bigint)             as unique_unsubscribes,
    cast(Unique_Placed_Order as bigint)             as unique_orders,
    cast(Total_Placed_Order as bigint)              as total_orders,
    cast(Total_Placed_Order_Value as decimal(12,2)) as order_value,
    Tags                                            as tags,
    try_cast(_weld_synced as timestamp)             as synced_at

from src
where {{ as_of_filter('_weld_synced', 'Run_Date') }}
```

- [ ] **Step 5: Build and test**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe build --profiles-dir . --select stg_ads_daily stg_email_flows
```

Expected: 2 models built, all tests PASS.

- [ ] **Step 6: Verify Meta conversions are NULL, not 0, and that the Meta gap survives**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe show --profiles-dir . --inline "
select platform,
       count(*) as rows,
       count(distinct ad_date) as days,
       count(platform_conversions) as non_null_conv
from {{ ref('stg_ads_daily') }}
group by platform"
```

Expected exactly:

- `meta`: 6 campaigns × 363 days = 2178 rows, **363** distinct days, `non_null_conv = 0`
- `google`: 5 campaigns × 365 days = 1825 rows, **365** distinct days, `non_null_conv = 1825`

Meta showing 363 days rather than 365 is the planted gap (2025-03-15 and 2025-03-16). It must survive into staging — Task 9 depends on it.

- [ ] **Step 6a: Add a regression test for the NULL-vs-zero invariant**

The NULL-not-zero rule is the most important requirement in this task, and a one-off
verification query does not protect it. Someone "helpfully" wrapping the rollup in
`coalesce(..., 0)` later would reintroduce the exact defect while the suite stayed green.

Create `dbt/tests/assert_meta_reports_no_conversions.sql`:

```sql
-- Meta reports NO conversion data at all. Its conversion columns must be
-- NULL, never 0: zero means "measured, and it was none", NULL means
-- "not measured". Coercing to zero makes Meta's ROAS read as 0.0 rather
-- than unknown and silently corrupts every cross-platform comparison
-- the engine makes downstream.
--
-- Guards the invariant in BOTH directions, because a union that silently
-- dropped Google's conversions would be just as wrong.

select
    'meta_has_conversion_data' as violation,
    platform,
    campaign_id,
    ad_date
from {{ ref('stg_ads_daily') }}
where platform = 'meta'
  and (platform_conversions is not null or platform_conversion_value is not null)

union all

select
    'google_missing_conversion_data' as violation,
    platform,
    campaign_id,
    ad_date
from {{ ref('stg_ads_daily') }}
where platform = 'google'
  and (platform_conversions is null or platform_conversion_value is null)
```

Run it and confirm it PASSES (zero rows):

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe test --profiles-dir . --select assert_meta_reports_no_conversions
```

Then confirm it actually bites: temporarily change `cast(null as double) as platform_conversions`
in the meta CTE to `cast(0 as double)`, re-run the test, and verify it FAILS with 2178 rows.
Revert the change immediately and re-run to confirm it passes again. A test that cannot fail
is not a test.

- [ ] **Step 7: Commit**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task"
git add -A
git commit -m "feat: add unioned ad spend and email flow staging models"
```

---

## Task 5: `dim_date` and `dim_campaign`

**Files:**

- Create: `dbt/models/core/dim_date.sql`, `dbt/models/core/dim_campaign.sql`, `dbt/models/core/_core.yml`

**Interfaces:**

- Produces:
  - `dim_date`: `date_day DATE` (PK), `year`, `month`, `year_month VARCHAR`, `iso_dow`, `iso_week`, `week_start DATE`, `month_start DATE`, `is_weekend BOOLEAN`, `is_peak_season BOOLEAN`.
  - `dim_campaign`: `campaign_key VARCHAR` (PK, `platform || ':' || campaign_id`), `platform`, `campaign_id`, `campaign_name`, `funnel_stage VARCHAR`.

- [ ] **Step 1: Write the failing tests**

Create `dbt/models/core/_core.yml`:

```yaml
version: 2

models:
  - name: dim_date
    description: "Date spine from the first order date through var('as_of_date')."
    columns:
      - name: date_day
        data_tests: [unique, not_null]
      - name: is_peak_season
        description: "November and December, the observed demand peak."
        data_tests: [not_null]

  - name: dim_campaign
    description: "One row per platform campaign, with funnel stage parsed from the name."
    columns:
      - name: campaign_key
        data_tests: [unique, not_null]
      - name: funnel_stage
        data_tests:
          - accepted_values:
              arguments:
                values: ['prospecting', 'retargeting', 'catalogue', 'brand', 'non_brand', 'shopping', 'pmax', 'other']
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe test --profiles-dir . --select dim_date dim_campaign
```

Expected: the test does NOT pass. dbt will do one of two things depending on how the
reference resolves: raise a compilation error naming the missing model, or emit
`WARNING: Did not find matching node for patch` and report "Nothing to do" (NO-OP).
Either outcome confirms the test cannot pass before the model exists. What matters is that
it does not report PASS — do not treat a NO-OP as a failure of this step.

- [ ] **Step 3: Create `dbt/models/core/dim_date.sql`**

```sql
{#
  A generous fixed spine, trimmed to the observed data window and the
  as_of cursor. Generating from a literal range keeps the model
  deterministic; the join to bounds is what makes it honest.
#}

with bounds as (
    select
        min(order_date)                                     as first_day,
        -- Clamp to the last day that actually has data. The as_of cursor sits
        -- one day past the period close (see dbt_project.yml), so using it
        -- directly would append a trailing empty day to the spine and to every
        -- mart built on it.
        least(cast('{{ var("as_of_date") }}' as date),
              max(order_date))                              as last_day
    from {{ ref('stg_orders') }}
),

spine as (
    select cast(unnest as date) as date_day
    from unnest(generate_series(date '2024-01-01', date '2026-12-31', interval 1 day))
)

select
    s.date_day,
    extract(year from s.date_day)                       as year,
    extract(month from s.date_day)                      as month,
    strftime(s.date_day, '%Y-%m')                       as year_month,
    extract(isodow from s.date_day)                     as iso_dow,
    extract(week from s.date_day)                       as iso_week,
    cast(date_trunc('week', s.date_day) as date)        as week_start,
    cast(date_trunc('month', s.date_day) as date)       as month_start,
    extract(isodow from s.date_day) in (6, 7)           as is_weekend,
    extract(month from s.date_day) in (11, 12)          as is_peak_season

from spine s
cross join bounds b
where s.date_day between b.first_day and b.last_day
```

If `unnest(generate_series(...))` errors on this DuckDB build, substitute:

```sql
spine as (
    select cast(range as date) as date_day
    from range(date '2024-01-01', date '2027-01-01', interval 1 day)
)
```

- [ ] **Step 4: Create `dbt/models/core/dim_campaign.sql`**

```sql
{#
  Funnel stage is parsed from the campaign name. Both platforms use a
  "Stage - Detail" naming convention, which makes stage a reliable
  dimension for rolling spend up by intent rather than by campaign.
#}

select distinct
    platform || ':' || campaign_id       as campaign_key,
    platform,
    campaign_id,
    campaign_name,
    case
        when lower(campaign_name) like 'prospecting%'  then 'prospecting'
        when lower(campaign_name) like 'retargeting%'  then 'retargeting'
        when lower(campaign_name) like 'dpa%'          then 'catalogue'
        when lower(campaign_name) like 'brand%'        then 'brand'
        when lower(campaign_name) like 'non-brand%'    then 'non_brand'
        when lower(campaign_name) like 'shopping%'     then 'shopping'
        when lower(campaign_name) like 'pmax%'         then 'pmax'
        else 'other'
    end                                  as funnel_stage

from {{ ref('stg_ads_daily') }}
```

- [ ] **Step 5: Build and test**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe build --profiles-dir . --select dim_date dim_campaign
```

Expected: 2 models built, all tests PASS. `dim_date` should hold 365 rows; `dim_campaign` 11 rows (6 Meta + 5 Google), with no row falling into `other`.

- [ ] **Step 6: Verify no campaign fell through to 'other'**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe show --profiles-dir . --inline "select funnel_stage, count(*) as n from {{ ref('dim_campaign') }} group by 1 order by 1"
```

Expected: eight or fewer stages listed, `other` absent.

- [ ] **Step 7: Commit**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task"
git add -A
git commit -m "feat: add date and campaign dimensions"
```

---

## Task 6: `dim_customer` and `dim_product`

**Files:**

- Create: `dbt/models/core/dim_customer.sql`, `dbt/models/core/dim_product.sql`
- Modify: `dbt/models/core/_core.yml`

**Interfaces:**

- Produces:
  - `dim_customer`: `customer_id` (PK), `customer_email`, `has_valid_email`, `is_marketable BOOLEAN`, `email_consent_state`, `signup_date`, `first_order_date DATE`, `first_order_month DATE`, `acquisition_channel VARCHAR`, `accepts_marketing`, `source_order_count`, `source_total_spent`.
  - `dim_product`: `variant_id` (PK), `product_id`, `product_title`, `product_type`, `sku`, `variant_title`, `price`, `unit_cost`, `margin_pct`, `inventory_quantity`, `status`, `vendor`.

- [ ] **Step 1: Write the failing tests**

Append to `dbt/models/core/_core.yml`:

```yaml
  - name: dim_customer
    description: "One row per customer. Acquisition channel is the channel of their FIRST NON-CANCELLED order."
    columns:
      - name: customer_id
        data_tests: [unique, not_null]
      - name: acquisition_channel
        data_tests:
          - accepted_values:
              arguments:
                values: ['meta', 'google', 'tiktok', 'unattributed']
      - name: is_marketable
        description: "Has a valid email AND has consented. Excludes the 623 blank-email records."
        data_tests: [not_null]

  - name: dim_product
    columns:
      - name: variant_id
        data_tests: [unique, not_null]
      - name: margin_pct
        data_tests: [not_null]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe test --profiles-dir . --select dim_customer dim_product
```

Expected: the test does NOT pass. dbt will do one of two things depending on how the
reference resolves: raise a compilation error naming the missing model, or emit
`WARNING: Did not find matching node for patch` and report "Nothing to do" (NO-OP).
Either outcome confirms the test cannot pass before the model exists. What matters is that
it does not report PASS — do not treat a NO-OP as a failure of this step.

- [ ] **Step 3: Create `dbt/models/core/dim_customer.sql`**

```sql
{#
  Customers are keyed on customer_id ONLY.

  orders.email differs from customers.email for 86% of orders (same local
  part, different domain). Any join or dedup on email corrupts every
  customer-level metric in the warehouse.

  Acquisition channel and first order date derive from non-cancelled
  orders only, so a customer whose only order was cancelled has a NULL
  first_order_date and does not enter any cohort.
#}

with first_order as (

    select
        customer_id,
        min(order_date)                                 as first_order_date,
        arg_min(channel, created_at)                    as acquisition_channel
    from {{ ref('stg_orders') }}
    where not is_cancelled
    group by customer_id

)

select
    c.customer_id,
    c.customer_email,
    c.has_valid_email,
    c.has_valid_email and c.email_consent_state = 'subscribed'  as is_marketable,
    c.email_consent_state,
    c.accepts_marketing,
    c.signup_date,
    f.first_order_date,
    cast(date_trunc('month', f.first_order_date) as date)       as first_order_month,
    coalesce(f.acquisition_channel, 'unattributed')             as acquisition_channel,
    c.source_order_count,
    c.source_total_spent

from {{ ref('stg_customers') }} c
left join first_order f
    on f.customer_id = c.customer_id
```

- [ ] **Step 4: Create `dbt/models/core/dim_product.sql`**

```sql
select
    variant_id,
    product_id,
    product_title,
    product_type,
    sku,
    variant_title,
    price,
    compare_at_price,
    unit_cost,
    margin_pct,
    weight_grams,
    inventory_quantity,
    status,
    vendor

from {{ ref('stg_products') }}
```

- [ ] **Step 5: Build and test**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe build --profiles-dir . --select dim_customer dim_product
```

Expected: 2 models built, all tests PASS.

- [ ] **Step 6: Verify the marketable base excludes blank emails**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe show --profiles-dir . --inline "
select count(*) as customers,
       sum(case when not has_valid_email then 1 else 0 end) as blank_email,
       sum(case when is_marketable then 1 else 0 end) as marketable
from {{ ref('dim_customer') }}"
```

Expected exactly: `customers = 20817`, `blank_email = 623`, `marketable = 9071`.

Note: `marketable` equals the raw subscribed count here because all 623 blank-email customers are already `not_subscribed`. Verified against the CSV. Do not treat the equality as a bug.

- [ ] **Step 7: Commit**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task"
git add -A
git commit -m "feat: add customer and product dimensions"
```

---

## Task 7: `fct_order` and `fct_order_line`

**Files:**

- Create: `dbt/models/core/fct_order.sql`, `dbt/models/core/fct_order_line.sql`
- Create: `dbt/tests/assert_margin_not_above_revenue.sql`
- Modify: `dbt/models/core/_core.yml`

**Interfaces:**

- Produces:
  - `fct_order`: `order_id` (PK), `customer_id`, `order_date`, `created_at`, `channel`, `is_cancelled`, `financial_status`, `total_price`, `subtotal_price`, `total_discounts`, `total_tax`, `total_shipping`, `is_first_order BOOLEAN`.
  - `fct_order_line`: `order_line_id` (PK), `order_id`, `customer_id`, `variant_id`, `order_date`, `channel`, `is_cancelled`, `quantity`, `gross_revenue_incl_vat DECIMAL(12,2)`, `line_discount DECIMAL(12,2)`, `net_revenue_incl_vat DECIMAL(12,2)`, `net_revenue DECIMAL(12,2)` (ex-VAT), `cogs DECIMAL(12,2)`, `contribution_margin DECIMAL(12,2)`. Downstream models read `net_revenue` and `contribution_margin`; the `_incl_vat` columns exist only for reconciliation against source order totals.

- [ ] **Step 1: Write the failing tests**

Append to `dbt/models/core/_core.yml`:

```yaml
  - name: fct_order
    columns:
      - name: order_id
        data_tests: [unique, not_null]
      - name: customer_id
        data_tests:
          - relationships:
              arguments:
                to: ref('dim_customer')
                field: customer_id
      - name: order_date
        data_tests:
          - relationships:
              arguments:
                to: ref('dim_date')
                field: date_day

  - name: fct_order_line
    description: "Line grain with COGS and contribution margin. All downstream value metrics are margin-based."
    columns:
      - name: order_line_id
        data_tests: [unique, not_null]
      - name: variant_id
        data_tests:
          - relationships:
              arguments:
                to: ref('dim_product')
                field: variant_id
      - name: contribution_margin
        data_tests: [not_null]
```

Create `dbt/tests/assert_margin_not_above_revenue.sql`:

```sql
-- Contribution margin can never exceed net revenue. If it does, COGS has
-- been joined wrongly or a discount has been double-counted.
select
    order_line_id,
    net_revenue,
    contribution_margin
from {{ ref('fct_order_line') }}
where contribution_margin > net_revenue
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe test --profiles-dir . --select fct_order fct_order_line
```

Expected: the test does NOT pass. dbt will do one of two things depending on how the
reference resolves: raise a compilation error naming the missing model, or emit
`WARNING: Did not find matching node for patch` and report "Nothing to do" (NO-OP).
Either outcome confirms the test cannot pass before the model exists. What matters is that
it does not report PASS — do not treat a NO-OP as a failure of this step.

- [ ] **Step 3: Create `dbt/models/core/fct_order.sql`**

```sql
with first_order as (
    select customer_id, min(created_at) as first_created_at
    from {{ ref('stg_orders') }}
    where not is_cancelled
    group by customer_id
)

select
    o.order_id,
    o.customer_id,
    o.order_date,
    o.created_at,
    o.channel,
    o.is_cancelled,
    o.financial_status,
    o.fulfillment_status,
    o.total_price,
    o.subtotal_price,
    o.total_discounts,
    o.total_line_items_price,
    o.total_tax,
    o.total_shipping,
    o.country_code,
    o.city,
    -- A customer's acquiring order. Cancelled orders never acquire.
    (not o.is_cancelled and o.created_at = f.first_created_at) as is_first_order

from {{ ref('stg_orders') }} o
left join first_order f
    on f.customer_id = o.customer_id
```

- [ ] **Step 4: Create `dbt/models/core/fct_order_line.sql`**

```sql
{#
  The margin fact table.

  Ex-VAT product margins range 64.0% to 82.0%, so revenue-based and
  margin-based conclusions diverge materially. Every downstream value
  metric reads contribution_margin, not net_revenue.

  VAT: source line values are VAT-INCLUSIVE (taxes_included is true on
  every order; total_tax = subtotal_price / 6 exactly, i.e. 20% already
  inside the price). products.cost is an EX-VAT cost price. Subtracting
  one from the other directly overstates margin by 6 to 8 points per
  variant, so net_revenue divides out VAT first.

  Both figures are kept: net_revenue_incl_vat reconciles to the source
  order totals, net_revenue is the true revenue that margin is taken on.

  COGS is unit_cost x quantity. The catalogue carries a single current
  cost per variant, so this is a point-in-time cost applied historically -
  noted as an assumption in the README.
#}

select
    l.order_line_id,
    l.order_id,
    o.customer_id,
    l.variant_id,
    l.product_id,
    l.sku,
    o.order_date,
    o.channel,
    o.is_cancelled,
    l.quantity,

    -- VAT-inclusive, reconciles to orders.total_line_items_price
    cast(l.unit_price * l.quantity as decimal(12,2))         as gross_revenue_incl_vat,
    l.total_discount                                          as line_discount,
    cast(l.unit_price * l.quantity - l.total_discount
         as decimal(12,2))                                    as net_revenue_incl_vat,

    -- Ex-VAT. This is the revenue every downstream metric uses.
    cast((l.unit_price * l.quantity - l.total_discount)
         / (1 + {{ var('vat_rate') }}) as decimal(12,2))      as net_revenue,
    cast(p.unit_cost * l.quantity as decimal(12,2))          as cogs,
    cast((l.unit_price * l.quantity - l.total_discount)
         / (1 + {{ var('vat_rate') }})
         - p.unit_cost * l.quantity as decimal(12,2))         as contribution_margin

from {{ ref('stg_order_lines') }} l
inner join {{ ref('fct_order') }} o
    on o.order_id = l.order_id
inner join {{ ref('dim_product') }} p
    on p.variant_id = l.variant_id
```

- [ ] **Step 5: Build and test**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe build --profiles-dir . --select fct_order fct_order_line
```

Expected: 2 models built, all tests PASS including `assert_margin_not_above_revenue`.

- [ ] **Step 6: Verify line totals reconcile to order totals**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe show --profiles-dir . --inline "
with l as (
    select order_id,
           sum(gross_revenue_incl_vat) as line_gross,
           sum(line_discount)          as line_disc
    from {{ ref('fct_order_line') }} group by order_id
)
select count(*) as mismatched_orders
from l join {{ ref('fct_order') }} o using (order_id)
where abs(l.line_gross - o.total_line_items_price) > 0.01
   or abs(l.line_disc  - o.total_discounts) > 0.01"
```

Expected: `mismatched_orders = 0`. This has been verified directly against the CSVs — line sums match order totals exactly, with zero variance across all 26,553 orders. A non-zero result here therefore means a join is fanning out, not that the source disagrees.

- [ ] **Step 6a: Verify VAT has actually been removed**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe show --profiles-dir . --inline "
select
    round(sum(net_revenue_incl_vat), 2)  as incl_vat,
    round(sum(net_revenue), 2)           as ex_vat,
    round(sum(contribution_margin), 2)   as margin,
    round(sum(contribution_margin) / sum(net_revenue), 3) as margin_pct
from {{ ref('fct_order_line') }} where not is_cancelled"
```

Expected: `incl_vat` = **1453925.32** exactly, `margin_pct` ≈ **0.706**, and `ex_vat` ≈ **1211690** — note this is about £86 ABOVE `incl_vat / 1.2` (1211604.43), which is correct and expected. `net_revenue` is rounded to pence per line, and because almost every price ends in .99 those roundings are systematically upward (31,514 lines up vs 9,542 down). Per-line rounding is what an invoice actually shows, so it is the right behaviour. Do NOT try to make `ex_vat` equal `incl_vat / 1.2`.

If `margin_pct` comes out above 0.82 the VAT divisor is missing.

- [ ] **Step 7: Commit**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task"
git add -A
git commit -m "feat: add order and order line facts with contribution margin"
```

---

## Task 8: `fct_ad_spend_daily` and `fct_email_flow_weekly`

**Files:**

- Create: `dbt/models/core/fct_ad_spend_daily.sql`, `dbt/models/core/fct_email_flow_weekly.sql`
- Modify: `dbt/models/core/_core.yml`

**Interfaces:**

- Produces:
  - `fct_ad_spend_daily`: `campaign_key`, `platform`, `campaign_id`, `funnel_stage`, `ad_date`, `impressions`, `clicks`, `spend`, `reach`, `frequency`, `platform_conversions`, `platform_conversion_value`, `cpc DECIMAL(12,4)`, `cpm DECIMAL(12,4)`, `ctr DECIMAL(8,6)`.
  - `fct_email_flow_weekly`: `flow_id`, `flow_name`, `message_id`, `week_start`, `recipients`, `unique_opens`, `unique_clicks`, `unique_unsubscribes`, `unique_orders`, `order_value`, `open_rate`, `click_rate`, `conversion_rate`, `revenue_per_recipient`.

- [ ] **Step 1: Write the failing tests**

Append to `dbt/models/core/_core.yml`:

```yaml
  - name: fct_ad_spend_daily
    description: "Daily campaign spend with derived efficiency metrics. CPC = CPM / (1000 * CTR)."
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [campaign_key, ad_date]
    columns:
      - name: campaign_key
        data_tests:
          - relationships:
              arguments:
                to: ref('dim_campaign')
                field: campaign_key
      - name: ad_date
        data_tests:
          - relationships:
              arguments:
                to: ref('dim_date')
                field: date_day

  - name: fct_email_flow_weekly
    description: "Weekly flow engagement with derived rates."
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [flow_id, message_id, week_start]
    columns:
      - name: open_rate
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
                max_value: 1
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe test --profiles-dir . --select fct_ad_spend_daily fct_email_flow_weekly
```

Expected: the test does NOT pass. dbt will do one of two things depending on how the
reference resolves: raise a compilation error naming the missing model, or emit
`WARNING: Did not find matching node for patch` and report "Nothing to do" (NO-OP).
Either outcome confirms the test cannot pass before the model exists. What matters is that
it does not report PASS — do not treat a NO-OP as a failure of this step.

- [ ] **Step 3: Create `dbt/models/core/fct_ad_spend_daily.sql`**

```sql
{#
  Derived efficiency metrics live here, not in the marts, because the
  CPC decomposition detector needs CPM and CTR at campaign-day grain.

  CPC = CPM / (1000 * CTR) is an identity, so a CPC movement can always
  be attributed to its CPM component (auction price) and its CTR
  component (creative relevance). That decomposition is what turns
  "CAC is up" into an actionable recommendation.
#}

select
    a.platform || ':' || a.campaign_id                  as campaign_key,
    a.platform,
    a.campaign_id,
    c.funnel_stage,
    a.ad_date,
    a.impressions,
    a.clicks,
    a.spend,
    a.reach,
    a.frequency,
    a.platform_conversions,
    a.platform_conversion_value,

    cast(a.spend / nullif(a.clicks, 0) as decimal(12,4))            as cpc,
    cast(1000.0 * a.spend / nullif(a.impressions, 0)
         as decimal(12,4))                                          as cpm,
    cast(a.clicks * 1.0 / nullif(a.impressions, 0)
         as decimal(8,6))                                           as ctr

from {{ ref('stg_ads_daily') }} a
inner join {{ ref('dim_campaign') }} c
    on c.campaign_key = a.platform || ':' || a.campaign_id
```

- [ ] **Step 4: Create `dbt/models/core/fct_email_flow_weekly.sql`**

```sql
{#
  Engagement rates AND conversion rate sit side by side deliberately.

  The email decay detector compares their trends: if opens fall while
  conversion holds, the flows are still monetising and the decay is a
  measurement or deliverability artifact, not lost demand.
#}

select
    flow_id,
    flow_name,
    message_id,
    message_name,
    week_start,
    flow_status,
    recipients,
    unique_opens,
    unique_clicks,
    unique_unsubscribes,
    unique_orders,
    total_orders,
    order_value,

    cast(unique_opens  * 1.0 / nullif(recipients, 0) as decimal(8,6)) as open_rate,
    cast(unique_clicks * 1.0 / nullif(recipients, 0) as decimal(8,6)) as click_rate,
    cast(unique_orders * 1.0 / nullif(recipients, 0) as decimal(8,6)) as conversion_rate,
    cast(unique_unsubscribes * 1.0 / nullif(recipients, 0)
         as decimal(8,6))                                             as unsubscribe_rate,
    cast(order_value / nullif(recipients, 0) as decimal(12,4))        as revenue_per_recipient

from {{ ref('stg_email_flows') }}
```

- [ ] **Step 5: Build and test**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe build --profiles-dir . --select fct_ad_spend_daily fct_email_flow_weekly
```

Expected: 2 models built, all tests PASS.

- [ ] **Step 6: Verify the CPC identity holds**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe show --profiles-dir . --inline "
select count(*) as broken
from {{ ref('fct_ad_spend_daily') }}
where clicks > 0 and impressions > 0
  and abs(cpc - cpm / (1000.0 * ctr)) > 0.01"
```

Expected: `broken = 0`.

- [ ] **Step 7: Commit**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task"
git add -A
git commit -m "feat: add ad spend and email flow facts with derived rates"
```

---

## Task 9: `mart_data_quality` and the completeness test

**Files:**

- Create: `dbt/models/marts/mart_data_quality.sql`, `dbt/models/marts/_marts.yml`
- Create: `dbt/tests/assert_source_date_completeness.sql`

**Interfaces:**

- Produces: `mart_data_quality` with columns `source_name VARCHAR`, `date_day DATE`, `expected BOOLEAN`, `observed BOOLEAN`, `is_gap BOOLEAN`, `row_count BIGINT`, `issue_type VARCHAR`.

This is the machinery that lets the detection layer mechanically separate a data-quality incident from a commercial event. It must exist before any detector is written.

- [ ] **Step 1: Write the failing test**

Create `dbt/tests/assert_source_date_completeness.sql`:

```sql
-- Every daily source must have a row for every day in the spine.
--
-- THIS TEST IS EXPECTED TO FAIL ON FIRST RUN. meta_ads_daily is missing
-- 2025-03-15 and 2025-03-16. That failure is the point: it proves the
-- gap is detected mechanically rather than noticed by eye. Once
-- confirmed, it is configured to warn rather than error (Step 6) so the
-- rest of the suite stays green while the gap remains visible.

select
    source_name,
    date_day
from {{ ref('mart_data_quality') }}
where is_gap
```

Create `dbt/models/marts/_marts.yml`:

```yaml
version: 2

models:
  - name: mart_data_quality
    description: >
      Per-source, per-day completeness and drift audit. Detectors join to
      this table to reclassify a signal as DATA_QUALITY when its window
      overlaps a known incident.
    columns:
      - name: source_name
        data_tests:
          - accepted_values:
              arguments:
                values: ['orders', 'meta_ads_daily', 'google_ads_daily']
      - name: date_day
        data_tests: [not_null]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe test --profiles-dir . --select mart_data_quality
```

Expected: the test does NOT pass. dbt will do one of two things depending on how the
reference resolves: raise a compilation error naming the missing model, or emit
`WARNING: Did not find matching node for patch` and report "Nothing to do" (NO-OP).
Either outcome confirms the test cannot pass before the model exists. What matters is that
it does not report PASS — do not treat a NO-OP as a failure of this step.

- [ ] **Step 3: Create `dbt/models/marts/mart_data_quality.sql`**

```sql
{#
  Completeness audit for the daily sources.

  A "gap" is a day present in the date spine but absent from the source.
  This is what catches meta_ads_daily's missing 2025-03-15 and
  2025-03-16, which a naive detector would otherwise read as spend
  collapsing to zero.
#}

with spine as (
    select date_day from {{ ref('dim_date') }}
),

orders_daily as (
    select order_date as date_day, count(*) as row_count
    from {{ ref('fct_order') }}
    group by order_date
),

meta_daily as (
    select ad_date as date_day, count(*) as row_count
    from {{ ref('fct_ad_spend_daily') }}
    where platform = 'meta'
    group by ad_date
),

google_daily as (
    select ad_date as date_day, count(*) as row_count
    from {{ ref('fct_ad_spend_daily') }}
    where platform = 'google'
    group by ad_date
),

combined as (

    select 'orders' as source_name, s.date_day, o.row_count
    from spine s left join orders_daily o on o.date_day = s.date_day

    union all

    select 'meta_ads_daily', s.date_day, m.row_count
    from spine s left join meta_daily m on m.date_day = s.date_day

    union all

    select 'google_ads_daily', s.date_day, g.row_count
    from spine s left join google_daily g on g.date_day = s.date_day

)

select
    source_name,
    date_day,
    true                                        as expected,
    row_count is not null                       as observed,
    row_count is null                           as is_gap,
    coalesce(row_count, 0)                      as row_count,
    case
        when row_count is null then 'missing_day'
        else 'ok'
    end                                         as issue_type

from combined
```

- [ ] **Step 4: Build the model and run the completeness test**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe run --profiles-dir . --select mart_data_quality
../venv/Scripts/dbt.exe test --profiles-dir . --select assert_source_date_completeness
```

Expected: **FAIL with exactly 2 rows** — `meta_ads_daily / 2025-03-15` and `meta_ads_daily / 2025-03-16`.

This failure is the deliverable for this task. If it passes, the test is not working; if it fails with more than two rows, the date spine or a fact table is wrong.

- [ ] **Step 5: Record the confirmed gap**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe show --profiles-dir . --inline "
select source_name, date_day, issue_type
from {{ ref('mart_data_quality') }}
where is_gap
order by source_name, date_day"
```

Expected: the two Meta rows and nothing else. Copy this output into the README when Task 15 is reached.

- [ ] **Step 6: Downgrade the test to a warning so the suite stays green**

Add a config block at the top of `dbt/tests/assert_source_date_completeness.sql`:

```sql
{{ config(severity='warn') }}
```

Then re-run:

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe test --profiles-dir . --select assert_source_date_completeness
```

Expected: `WARN 2` rather than `ERROR`. The gap stays visible in every build without blocking it.

- [ ] **Step 7: Commit**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task"
git add -A
git commit -m "feat: add data quality mart and source completeness test

The completeness test detects the two missing meta_ads_daily days
(2025-03-15, 2025-03-16) mechanically. Configured to warn so the gap
stays visible in every build without blocking it."
```

---

## Task 10: `mart_daily_trading`

**Files:**

- Create: `dbt/models/marts/mart_daily_trading.sql`
- Modify: `dbt/models/marts/_marts.yml`

**Interfaces:**

- Produces: `mart_daily_trading`, grain `date_day` × `channel`. Columns: `date_day`, `channel`, `orders BIGINT`, `new_customers BIGINT`, `returning_customers BIGINT`, `net_revenue DECIMAL(14,2)`, `contribution_margin DECIMAL(14,2)`, `aov DECIMAL(12,2)`, `ad_spend DECIMAL(14,2)`, `clicks BIGINT`, `channel_cac DECIMAL(12,2)`, `channel_roas DECIMAL(12,4)`, `blended_cac DECIMAL(12,2)`, `is_peak_season BOOLEAN`.

- [ ] **Step 1: Write the failing test**

Append to `dbt/models/marts/_marts.yml`:

```yaml
  - name: mart_daily_trading
    description: >
      Daily trading metrics by last-click channel. channel_cac and
      channel_roas are Tier C (attribution-dependent); blended_cac is
      Tier B and is repeated on every row of a given day.
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [date_day, channel]
    columns:
      - name: date_day
        data_tests:
          - not_null
          - relationships:
              arguments:
                to: ref('dim_date')
                field: date_day
      - name: channel
        data_tests:
          - accepted_values:
              arguments:
                values: ['meta', 'google', 'tiktok', 'unattributed']
      - name: contribution_margin
        data_tests: [not_null]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe test --profiles-dir . --select mart_daily_trading
```

Expected: the test does NOT pass. dbt will do one of two things depending on how the
reference resolves: raise a compilation error naming the missing model, or emit
`WARNING: Did not find matching node for patch` and report "Nothing to do" (NO-OP).
Either outcome confirms the test cannot pass before the model exists. What matters is that
it does not report PASS — do not treat a NO-OP as a failure of this step.

- [ ] **Step 3: Create `dbt/models/marts/mart_daily_trading.sql`**

```sql
{#
  The primary metric spine.

  Spend maps to a channel by platform: meta -> 'meta', google -> 'google'.
  TikTok drives 9% of orders but has NO cost file, so its CAC is
  structurally uncomputable and stays NULL. Unattributed (26.9% of
  orders) has no spend by definition.

  blended_cac = total ad spend / total new customers for the day, repeated
  on every channel row. It is the only complete cost measure and is the
  attribution-free cross-check on channel_cac.
#}

with order_facts as (

    select
        o.order_date                                    as date_day,
        o.channel,
        count(distinct o.order_id)                      as orders,
        count(distinct case when o.is_first_order
                            then o.customer_id end)     as new_customers,
        count(distinct case when not o.is_first_order
                            then o.customer_id end)     as returning_customers
    from {{ ref('fct_order') }} o
    where not o.is_cancelled
    group by o.order_date, o.channel

),

line_facts as (

    select
        order_date                                      as date_day,
        channel,
        sum(net_revenue)                                as net_revenue,
        sum(contribution_margin)                        as contribution_margin
    from {{ ref('fct_order_line') }}
    where not is_cancelled
    group by order_date, channel

),

spend_facts as (

    select
        ad_date                                         as date_day,
        platform                                        as channel,
        sum(spend)                                      as ad_spend,
        sum(clicks)                                     as clicks,
        sum(impressions)                                as impressions
    from {{ ref('fct_ad_spend_daily') }}
    group by ad_date, platform

),

daily_totals as (

    select
        date_day,
        sum(new_customers)                              as total_new_customers
    from order_facts
    group by date_day

),

daily_spend as (

    select
        date_day,
        sum(ad_spend)                                   as total_ad_spend
    from spend_facts
    group by date_day

),

grid as (
    select d.date_day, c.channel
    from {{ ref('dim_date') }} d
    cross join (select unnest(['meta','google','tiktok','unattributed']) as channel) c
)

select
    g.date_day,
    g.channel,
    coalesce(o.orders, 0)                               as orders,
    coalesce(o.new_customers, 0)                        as new_customers,
    coalesce(o.returning_customers, 0)                  as returning_customers,
    coalesce(l.net_revenue, 0)                          as net_revenue,
    coalesce(l.contribution_margin, 0)                  as contribution_margin,
    cast(l.net_revenue / nullif(o.orders, 0)
         as decimal(12,2))                              as aov,

    s.ad_spend,
    s.clicks,
    s.impressions,

    -- Tier C: depends on last-click attribution.
    cast(s.ad_spend / nullif(o.new_customers, 0)
         as decimal(12,2))                              as channel_cac,
    cast(l.net_revenue / nullif(s.ad_spend, 0)
         as decimal(12,4))                              as channel_roas,
    cast(l.contribution_margin / nullif(s.ad_spend, 0)
         as decimal(12,4))                              as channel_margin_roas,

    -- Tier B: attribution-free, identical across a day's channel rows.
    cast(ds.total_ad_spend / nullif(dt.total_new_customers, 0)
         as decimal(12,2))                              as blended_cac,

    d.is_peak_season,
    d.iso_dow,
    d.week_start

from grid g
left join order_facts  o  on o.date_day = g.date_day and o.channel = g.channel
left join line_facts   l  on l.date_day = g.date_day and l.channel = g.channel
left join spend_facts  s  on s.date_day = g.date_day and s.channel = g.channel
left join daily_totals dt on dt.date_day = g.date_day
left join daily_spend  ds on ds.date_day = g.date_day
inner join {{ ref('dim_date') }} d on d.date_day = g.date_day
```

- [ ] **Step 4: Build and test**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe build --profiles-dir . --select mart_daily_trading
```

Expected: 1 model built, all tests PASS. Row count = 365 × 4 = 1460.

- [ ] **Step 5: Verify the mart reproduces the profiled monthly CAC figures**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe show --profiles-dir . --limit 24 --inline "
select strftime(date_day, '%Y-%m') as ym,
       channel,
       sum(ad_spend) as spend,
       sum(new_customers) as new_cust,
       round(sum(ad_spend) / nullif(sum(new_customers),0), 2) as cac
from {{ ref('mart_daily_trading') }}
where channel in ('meta','google')
group by 1,2 order by 2,1"
```

Expected, matching the profiling in the spec (section 2):

- meta 2024-07 CAC ≈ 19.82, meta 2025-06 CAC ≈ 34.74
- google 2024-07 CAC ≈ 11.73, google 2025-06 CAC ≈ 15.26

If these do not reproduce, the channel mapping or the `is_first_order` flag is wrong. Fix before proceeding — every downstream detector depends on this.

- [ ] **Step 6: Verify TikTok CAC is NULL, not zero or infinite**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe show --profiles-dir . --inline "
select channel,
       count(*) as days,
       count(ad_spend) as days_with_spend,
       count(channel_cac) as days_with_cac
from {{ ref('mart_daily_trading') }} group by 1 order by 1"
```

Expected: `tiktok` and `unattributed` show `days_with_spend = 0` and `days_with_cac = 0`.

- [ ] **Step 7: Commit**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task"
git add -A
git commit -m "feat: add daily trading mart with channel and blended CAC"
```

---

## Task 11: `mart_product_daily`

**Files:**

- Create: `dbt/models/marts/mart_product_daily.sql`
- Modify: `dbt/models/marts/_marts.yml`

**Interfaces:**

- Produces: `mart_product_daily`, grain `date_day` × `variant_id`. Columns: `date_day`, `variant_id`, `product_title`, `sku`, `units BIGINT`, `net_revenue`, `contribution_margin`, `velocity_7d DOUBLE`, `velocity_28d DOUBLE`, `velocity_ratio DOUBLE`, `inventory_quantity`, `days_of_cover DOUBLE`.

- [ ] **Step 1: Write the failing test**

Append to `dbt/models/marts/_marts.yml`:

```yaml
  - name: mart_product_daily
    description: >
      Daily product velocity with trailing windows and inventory cover.
      velocity_7d and velocity_28d are trailing mean units per day.
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [date_day, variant_id]
    columns:
      - name: variant_id
        data_tests:
          - relationships:
              arguments:
                to: ref('dim_product')
                field: variant_id
      - name: units
        data_tests: [not_null]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe test --profiles-dir . --select mart_product_daily
```

Expected: the test does NOT pass. dbt will do one of two things depending on how the
reference resolves: raise a compilation error naming the missing model, or emit
`WARNING: Did not find matching node for patch` and report "Nothing to do" (NO-OP).
Either outcome confirms the test cannot pass before the model exists. What matters is that
it does not report PASS — do not treat a NO-OP as a failure of this step.

- [ ] **Step 3: Create `dbt/models/marts/mart_product_daily.sql`**

```sql
{#
  Product velocity with a dense grid.

  The grid matters: a product that sells nothing on a given day must
  produce a zero row, not a missing row, or the trailing means silently
  skip days and overstate velocity.

  days_of_cover uses current inventory against trailing 28-day velocity.
  The catalogue provides only a current snapshot of inventory, so cover
  is only meaningful on the final as_of date - flagged in the README.
#}

with grid as (
    select d.date_day, p.variant_id
    from {{ ref('dim_date') }} d
    cross join {{ ref('dim_product') }} p
),

sold as (
    select
        order_date                                      as date_day,
        variant_id,
        sum(quantity)                                   as units,
        sum(net_revenue)                                as net_revenue,
        sum(contribution_margin)                        as contribution_margin
    from {{ ref('fct_order_line') }}
    where not is_cancelled
    group by order_date, variant_id
),

dense as (
    select
        g.date_day,
        g.variant_id,
        coalesce(s.units, 0)                            as units,
        coalesce(s.net_revenue, 0)                      as net_revenue,
        coalesce(s.contribution_margin, 0)              as contribution_margin
    from grid g
    left join sold s
        on s.date_day = g.date_day and s.variant_id = g.variant_id
)

select
    d.date_day,
    d.variant_id,
    p.product_title,
    p.sku,
    p.product_type,
    d.units,
    d.net_revenue,
    d.contribution_margin,

    avg(d.units) over (
        partition by d.variant_id order by d.date_day
        rows between 6 preceding and current row
    )                                                   as velocity_7d,

    avg(d.units) over (
        partition by d.variant_id order by d.date_day
        rows between 27 preceding and current row
    )                                                   as velocity_28d,

    avg(d.units) over (
        partition by d.variant_id order by d.date_day
        rows between 6 preceding and current row
    ) / nullif(avg(d.units) over (
        partition by d.variant_id order by d.date_day
        rows between 27 preceding and current row
    ), 0)                                               as velocity_ratio,

    p.inventory_quantity,

    p.inventory_quantity / nullif(avg(d.units) over (
        partition by d.variant_id order by d.date_day
        rows between 27 preceding and current row
    ), 0)                                               as days_of_cover

from dense d
inner join {{ ref('dim_product') }} p
    on p.variant_id = d.variant_id
```

- [ ] **Step 4: Build and test**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe build --profiles-dir . --select mart_product_daily
```

Expected: 1 model built, tests PASS. Row count = 365 × 24 = 8760.

- [ ] **Step 5: Verify the D3 breakout and its inventory cover are visible**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe show --profiles-dir . --limit 30 --inline "
select strftime(date_day,'%Y-%m') as ym,
       product_title,
       sum(units) as units
from {{ ref('mart_product_daily') }}
where product_title = 'Vitamin D3 Drops'
group by 1,2 order by 1"
```

Expected: roughly 219 units in 2024-07 rising to roughly 701 in 2025-03 and holding above 600 thereafter.

```bash
../venv/Scripts/dbt.exe show --profiles-dir . --inline "
select sku, round(velocity_28d,1) as v28, inventory_quantity, round(days_of_cover,1) as cover
from {{ ref('mart_product_daily') }}
where date_day = (select max(date_day) from {{ ref('dim_date') }})
order by cover"
```

Expected: the two `VIT-D3` SKUs show a low `cover` relative to the rest of the catalogue.

- [ ] **Step 6: Commit**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task"
git add -A
git commit -m "feat: add product velocity mart with inventory cover"
```

---

## Task 12: `mart_cohort_retention` — the censoring-aware model

**Files:**

- Create: `dbt/models/marts/mart_cohort_retention.sql`
- Create: `dbt/tests/assert_censored_cohorts_have_null_retention.sql`
- Modify: `dbt/models/marts/_marts.yml`

**Interfaces:**

- Produces: `mart_cohort_retention`, grain `cohort_month` × `months_since`. Columns: `cohort_month DATE`, `months_since INT`, `cohort_size BIGINT`, `repeat_customers BIGINT`, `has_full_exposure BOOLEAN`, `retention_rate DOUBLE` (NULL when exposure is incomplete), `raw_retention_rate DOUBLE` (always populated, for contrast).

This is the most important model in the plan, and its purpose is subtler than "hide the collapse".

Verified against the CSVs: the retention decline is REAL for every cohort that has full exposure — 90-day repeat rate falls monotonically 31.8% (2024-07), 15.8% (2024-09), 2.4% (2024-11), 0.2% (2025-01), 0.0% (2025-03), all fully observed. Censoring explains ONLY the last two to three cohorts (2025-04 onward at 90 days), because the median gap to a second order is 100 days.

So `has_full_exposure` does not exist to explain the collapse away. It exists to separate the cohorts whose zero is a fact from the cohorts whose zero is merely unobserved — without it, the engine cannot tell a real collapse from an artifact, and would be wrong in one direction or the other.

- [ ] **Step 1: Write the failing test**

Create `dbt/tests/assert_censored_cohorts_have_null_retention.sql`:

```sql
-- A cohort whose observation window has not fully elapsed must not
-- report a retention rate. If it does, the censoring guard is broken and
-- the retention detector will fire on an artifact.

select
    cohort_month,
    months_since,
    has_full_exposure,
    retention_rate
from {{ ref('mart_cohort_retention') }}
where not has_full_exposure
  and retention_rate is not null
```

Append to `dbt/models/marts/_marts.yml`:

```yaml
  - name: mart_cohort_retention
    description: >
      Cohort retention with an explicit censoring guard. retention_rate is
      NULL wherever the observation window has not fully elapsed as of the
      LAST DATE WITH DATA (max(dim_date.date_day)) -- deliberately NOT as of
      var('as_of_date'), which sits one day past period close to absorb
      ad/email ingestion lag. raw_retention_rate is always populated and
      exists only to show the difference the guard makes.
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [cohort_month, months_since]
    columns:
      - name: cohort_size
        data_tests: [not_null]
      - name: has_full_exposure
        data_tests: [not_null]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe test --profiles-dir . --select mart_cohort_retention
```

Expected: the test does NOT pass. dbt will do one of two things depending on how the
reference resolves: raise a compilation error naming the missing model, or emit
`WARNING: Did not find matching node for patch` and report "Nothing to do" (NO-OP).
Either outcome confirms the test cannot pass before the model exists. What matters is that
it does not report PASS — do not treat a NO-OP as a failure of this step.

- [ ] **Step 3: Create `dbt/models/marts/mart_cohort_retention.sql`**

```sql
{#
  Cohort retention, censoring-aware.

  A cohort acquired in month M has FULL exposure at age K only if the
  entire month-K window has elapsed by as_of_date. Formally:

      last_day_of(M + K months) <= as_of_date

  Cohorts failing that test are right-censored: they have not had the
  opportunity to repeat, so their observed zero is an absence of
  evidence, not evidence of absence.

  Concretely: the median gap between a customer's first and second order
  is 100 days (p75 = 195), so cohorts acquired within ~3 months of the
  as_of cursor cannot yet have repeated.

  Exposure is measured against the LAST DATE WITH DATA (max(dim_date.date_day)),
  NOT against var('as_of_date'). The cursor deliberately sits one day past the
  period close to absorb ad/email ingestion lag, so comparing to it directly
  would credit a cohort with a day of observation that does not exist.

  IMPORTANT: censoring explains only the MOST RECENT cohorts. Cohorts
  through 2025-03 have full 90-day exposure and their retention decline
  (31.8% -> 0.0%) is genuine, not an artifact. The monthly repeat-order
  rate does stay near 24% all year, but that is carried by the Jul-Nov
  2024 cohorts still buying and MASKS the collapse rather than refuting
  it. This guard separates observed zeros from unobserved ones; it does
  not explain the decline away.
#}

with customers as (

    select
        customer_id,
        first_order_month                               as cohort_month
    from {{ ref('dim_customer') }}
    where first_order_date is not null

),

cohort_sizes as (
    select cohort_month, count(*) as cohort_size
    from customers
    group by cohort_month
),

orders as (

    select
        c.cohort_month,
        c.customer_id,
        cast(date_trunc('month', o.order_date) as date) as order_month
    from {{ ref('fct_order') }} o
    inner join customers c on c.customer_id = o.customer_id
    where not o.is_cancelled

),

activity as (

    select
        cohort_month,
        datediff('month', cohort_month, order_month)    as months_since,
        count(distinct customer_id)                     as active_customers
    from orders
    group by cohort_month, datediff('month', cohort_month, order_month)

),

ages as (
    select unnest(generate_series(0, 11)) as months_since
),

grid as (
    select s.cohort_month, s.cohort_size, a.months_since
    from cohort_sizes s
    cross join ages a
),

exposure as (

    select
        g.cohort_month,
        g.cohort_size,
        g.months_since,
        coalesce(act.active_customers, 0)               as active_customers,
        -- Last calendar day of the cohort's month-K window.
        cast(
            (g.cohort_month + to_months(cast(g.months_since + 1 as integer)))
            - interval 1 day
        as date)                                        as window_end
    from grid g
    left join activity act
        on act.cohort_month = g.cohort_month
       and act.months_since = g.months_since

)

select
    cohort_month,
    months_since,
    cohort_size,
    active_customers                                    as repeat_customers,
    window_end,
    window_end <= (select max(date_day) from {{ ref('dim_date') }})  as has_full_exposure,

    -- The guarded metric. NULL where the window has not elapsed.
    case
        when window_end <= (select max(date_day) from {{ ref('dim_date') }})
        then active_customers * 1.0 / nullif(cohort_size, 0)
    end                                                 as retention_rate,

    -- The unguarded metric, retained ONLY to demonstrate the artifact.
    -- Never consume this in a detector.
    active_customers * 1.0 / nullif(cohort_size, 0)     as raw_retention_rate

from exposure
where months_since > 0
```

- [ ] **Step 3a: Add the converse guard test**

The censoring test only checks one direction: censored implies NULL. A guard accidentally
inverted to return NULL *always* would satisfy it and pass silently, while destroying the
model's entire purpose. Guard both directions.

Create `dbt/tests/assert_exposed_cohorts_have_retention.sql`:

```sql
-- The converse of assert_censored_cohorts_have_null_retention.
--
-- A cohort-age whose observation window HAS fully elapsed must report a
-- retention_rate -- including a legitimate 0.0, which is an observed fact and
-- not a missing value. Without this test, a guard inverted to always-NULL
-- would pass the censoring test while silently erasing every real signal
-- this model exists to surface.

select
    cohort_month,
    months_since,
    has_full_exposure,
    retention_rate
from {{ ref('mart_cohort_retention') }}
where has_full_exposure
  and retention_rate is null
```

Expect PASS (zero rows): 66 of the 132 rows are fully exposed and all must carry a rate.

- [ ] **Step 4: Build and test**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe build --profiles-dir . --select mart_cohort_retention
```

Expected: 1 model built, all tests PASS including `assert_censored_cohorts_have_null_retention`.

If `to_months` is unavailable on this DuckDB build, substitute:

```sql
cast(date_trunc('month', g.cohort_month) + interval (g.months_since + 1) month - interval 1 day as date)
```

- [ ] **Step 5: Verify the censoring guard demonstrably works**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe show --profiles-dir . --limit 40 --inline "
select cohort_month,
       months_since,
       cohort_size,
       repeat_customers,
       has_full_exposure,
       round(raw_retention_rate, 4) as raw,
       round(retention_rate, 4)     as guarded
from {{ ref('mart_cohort_retention') }}
where months_since in (1, 3)
order by cohort_month, months_since"
```

Expected, and this is the whole point of the task:

- Early cohorts (2024-07, 2024-08) show `has_full_exposure = true` with both `raw` and `guarded` populated and non-trivial.
- Late cohorts (2025-04 onward at `months_since = 3`) show `has_full_exposure = false`, `raw = 0.0`, and `guarded = NULL`.

Where `raw` is 0.0 AND `has_full_exposure` is false, `guarded` must be NULL — that is the censoring guard working. Where `raw` is near 0.0 and `has_full_exposure` is TRUE (cohorts 2024-10 through 2025-03), `guarded` must be populated with that low value — that is a real collapse the guard must NOT hide. Capture this output for the notebook; both behaviours matter.

- [ ] **Step 6: Verify the true repeat rate is stable, as the contrast**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe show --profiles-dir . --limit 15 --inline "
with ranked as (
    select customer_id, order_id, order_date,
           row_number() over (partition by customer_id order by created_at) as seq
    from {{ ref('fct_order') }}
    where not is_cancelled
)
select strftime(order_date, '%Y-%m') as ym,
       count(*) as orders,
       sum(case when seq > 1 then 1 else 0 end) as repeat_orders,
       round(sum(case when seq > 1 then 1.0 else 0 end) / count(*), 3) as repeat_rate
from ranked group by 1 order by 1"
```

Expected: `repeat_rate` climbing from ~0.03 in 2024-07 (no prior customers exist yet) and stabilising at roughly 0.24 from 2024-10 onward.

This number does NOT prove retention held up — it is the trap. The rate is stable only because the Jul-Nov 2024 cohorts keep buying; essentially no 2025-acquired customer returns. Record both this figure and the per-cohort rates from Step 5 side by side: the contrast between them is the finding.

- [ ] **Step 7: Commit**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task"
git add -A
git commit -m "feat: add censoring-aware cohort retention mart

retention_rate is NULL wherever the observation window has not elapsed
as of the as_of cursor, separating cohorts whose zero is observed fact
from cohorts whose zero is merely unobserved. The decline itself is real:
90-day repeat rate falls 31.8% to 0.0% across fully-exposed cohorts."
```

---

## Task 13: `mart_ltv`

**Files:**

- Create: `dbt/models/marts/mart_ltv.sql`
- Modify: `dbt/models/marts/_marts.yml`

**Interfaces:**

- Produces: `mart_ltv`, grain `cohort_month` × `acquisition_channel` × `horizon_days`. Columns: `cohort_month`, `acquisition_channel`, `horizon_days INT`, `cohort_size BIGINT`, `has_full_exposure BOOLEAN`, `cum_net_revenue`, `cum_contribution_margin`, `ltv_revenue DOUBLE`, `ltv_margin DOUBLE`.

`ltv_margin` at `horizon_days = 60` is the figure the CAC/LTV breach detector compares against `channel_cac`.

- [ ] **Step 1: Write the failing test**

Append to `dbt/models/marts/_marts.yml`:

```yaml
  - name: mart_ltv
    description: >
      Contribution-margin LTV by acquisition cohort and channel at 30, 60
      and 90 day horizons. Censoring-aware: has_full_exposure is false
      where the horizon extends past var('as_of_date').
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [cohort_month, acquisition_channel, horizon_days]
    columns:
      - name: horizon_days
        data_tests:
          - accepted_values:
              arguments:
                values: [30, 60, 90]
      - name: cohort_size
        data_tests: [not_null]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe test --profiles-dir . --select mart_ltv
```

Expected: the test does NOT pass. dbt will do one of two things depending on how the
reference resolves: raise a compilation error naming the missing model, or emit
`WARNING: Did not find matching node for patch` and report "Nothing to do" (NO-OP).
Either outcome confirms the test cannot pass before the model exists. What matters is that
it does not report PASS — do not treat a NO-OP as a failure of this step.

- [ ] **Step 3: Create `dbt/models/marts/mart_ltv.sql`**

```sql
{#
  Contribution-margin LTV by acquisition cohort and channel.

  Margin-based, not revenue-based: ex-VAT catalogue margins range 64.0%
  to 82.0%, so a revenue LTV overstates the headroom above CAC by roughly
  1.4x - and by roughly 1.7x if VAT is also left in. Both errors push
  acquisition that is actually marginal onto the profitable side of the
  CAC threshold, which is precisely the decision this mart feeds.

  Censoring applies here as it does to retention: a cohort acquired in
  2025-06 has not had 90 days to spend by an as_of of 2025-06-30.
#}

with horizons as (
    select unnest([30, 60, 90]) as horizon_days
),

cohort as (
    select
        customer_id,
        first_order_month                               as cohort_month,
        first_order_date,
        acquisition_channel
    from {{ ref('dim_customer') }}
    where first_order_date is not null
),

cohort_sizes as (
    select
        cohort_month,
        acquisition_channel,
        count(*)                                        as cohort_size,
        max(first_order_date)                           as last_acquisition_date
    from cohort
    group by cohort_month, acquisition_channel
),

spend_within as (

    select
        c.cohort_month,
        c.acquisition_channel,
        h.horizon_days,
        sum(l.net_revenue)                              as cum_net_revenue,
        sum(l.contribution_margin)                      as cum_contribution_margin
    from cohort c
    cross join horizons h
    inner join {{ ref('fct_order_line') }} l
        on l.customer_id = c.customer_id
       and not l.is_cancelled
       and l.order_date >= c.first_order_date
       and l.order_date <  c.first_order_date + h.horizon_days
    group by c.cohort_month, c.acquisition_channel, h.horizon_days

)

select
    s.cohort_month,
    s.acquisition_channel,
    h.horizon_days,
    s.cohort_size,

    -- The last-acquired customer in the cohort must have had the full
    -- horizon to spend, or the cohort's LTV is understated.
    (s.last_acquisition_date + h.horizon_days)
        <= (select max(date_day) from {{ ref('dim_date') }})  as has_full_exposure,

    coalesce(w.cum_net_revenue, 0)                      as cum_net_revenue,
    coalesce(w.cum_contribution_margin, 0)              as cum_contribution_margin,

    coalesce(w.cum_net_revenue, 0) * 1.0
        / nullif(s.cohort_size, 0)                      as ltv_revenue,
    coalesce(w.cum_contribution_margin, 0) * 1.0
        / nullif(s.cohort_size, 0)                      as ltv_margin

from cohort_sizes s
cross join horizons h
left join spend_within w
    on w.cohort_month = s.cohort_month
   and w.acquisition_channel = s.acquisition_channel
   and w.horizon_days = h.horizon_days
```

- [ ] **Step 4: Build and test**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe build --profiles-dir . --select mart_ltv
```

Expected: 1 model built, all tests PASS.

- [ ] **Step 5: Verify 60-day margin LTV against Meta CAC**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe show --profiles-dir . --limit 30 --inline "
select cohort_month, acquisition_channel, cohort_size,
       has_full_exposure,
       round(ltv_revenue, 2) as ltv_rev,
       round(ltv_margin, 2)  as ltv_margin
from {{ ref('mart_ltv') }}
where horizon_days = 60 and acquisition_channel in ('meta','google')
order by acquisition_channel, cohort_month"
```

Expected: `ltv_margin` materially below `ltv_rev` (roughly 75-80% of it, matching catalogue margins), and `has_full_exposure = false` for the final one or two cohorts. Meta's late-cohort `ltv_margin` should sit below the Meta CAC of ~£34.74 computed in Task 10 — that gap is the CAC/LTV breach the detection layer will fire on.

- [ ] **Step 6: Commit**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task"
git add -A
git commit -m "feat: add censoring-aware contribution-margin LTV mart"
```

---

## Task 14: `mart_email_flow_weekly`

**Files:**

- Create: `dbt/models/marts/mart_email_flow_weekly.sql`
- Modify: `dbt/models/marts/_marts.yml`

**Interfaces:**

- Produces: `mart_email_flow_weekly`, grain `flow_id` × `week_start`. Columns: `flow_id`, `flow_name`, `week_start`, `recipients`, `open_rate`, `click_rate`, `conversion_rate`, `unsubscribe_rate`, `revenue_per_recipient`, `order_value`, plus 8-week trailing means `open_rate_8w`, `click_rate_8w`, `conversion_rate_8w`.

The trailing means are what the email decay detector compares: engagement falling while conversion holds is the signature of a measurement artifact rather than lost demand.

- [ ] **Step 1: Write the failing test**

Append to `dbt/models/marts/_marts.yml`:

```yaml
  - name: mart_email_flow_weekly
    description: >
      Flow-level weekly engagement, rolled up from message grain, with
      8-week trailing means. The decay detector compares the engagement
      trend against the conversion trend.
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [flow_id, week_start]
    columns:
      - name: flow_name
        data_tests:
          - accepted_values:
              arguments:
                values: ['Welcome Series', 'Cart Abandonment', 'Browse Abandonment', 'Post-Purchase', 'Replenishment', 'Win-Back']
      - name: conversion_rate
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
                max_value: 1
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe test --profiles-dir . --select mart_email_flow_weekly
```

Expected: the test does NOT pass. dbt will do one of two things depending on how the
reference resolves: raise a compilation error naming the missing model, or emit
`WARNING: Did not find matching node for patch` and report "Nothing to do" (NO-OP).
Either outcome confirms the test cannot pass before the model exists. What matters is that
it does not report PASS — do not treat a NO-OP as a failure of this step.

- [ ] **Step 3: Create `dbt/models/marts/mart_email_flow_weekly.sql`**

```sql
{#
  Flow-level weekly engagement.

  Rates are recomputed from summed numerators and denominators rather
  than averaged from message-level rates: averaging rates across messages
  with very different recipient counts weights a 50-recipient message
  equally with a 2,000-recipient one.
#}

with rolled as (

    select
        flow_id,
        flow_name,
        week_start,
        sum(recipients)                                 as recipients,
        sum(unique_opens)                               as unique_opens,
        sum(unique_clicks)                              as unique_clicks,
        sum(unique_unsubscribes)                        as unique_unsubscribes,
        sum(unique_orders)                              as unique_orders,
        sum(order_value)                                as order_value
    from {{ ref('fct_email_flow_weekly') }}
    group by flow_id, flow_name, week_start

),

rated as (

    select
        *,
        unique_opens  * 1.0 / nullif(recipients, 0)     as open_rate,
        unique_clicks * 1.0 / nullif(recipients, 0)     as click_rate,
        unique_orders * 1.0 / nullif(recipients, 0)     as conversion_rate,
        unique_unsubscribes * 1.0 / nullif(recipients, 0) as unsubscribe_rate,
        order_value / nullif(recipients, 0)             as revenue_per_recipient
    from rolled

)

select
    *,
    avg(open_rate) over (
        partition by flow_id order by week_start
        rows between 7 preceding and current row
    )                                                   as open_rate_8w,

    avg(click_rate) over (
        partition by flow_id order by week_start
        rows between 7 preceding and current row
    )                                                   as click_rate_8w,

    avg(conversion_rate) over (
        partition by flow_id order by week_start
        rows between 7 preceding and current row
    )                                                   as conversion_rate_8w

from rated
```

- [ ] **Step 4: Build and test**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe build --profiles-dir . --select mart_email_flow_weekly
```

Expected: 1 model built, all tests PASS.

- [ ] **Step 5: Verify the decay-with-flat-conversion signature is present**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe show --profiles-dir . --limit 30 --inline "
select strftime(week_start, '%Y-%m') as ym,
       round(avg(open_rate), 4)       as open_rate,
       round(avg(click_rate), 4)      as click_rate,
       round(avg(conversion_rate), 4) as conv_rate
from {{ ref('mart_email_flow_weekly') }}
where flow_name = 'Welcome Series'
group by 1 order by 1"
```

Expected, and this is the discrimination the detection layer depends on:

- `open_rate` falling from roughly 0.52 to roughly 0.40
- `click_rate` falling from roughly 0.12 to roughly 0.09
- `conv_rate` essentially FLAT at roughly 0.032 throughout

Engagement down, monetisation intact. If `conv_rate` also declines materially, re-check the rollup — averaging rates instead of recomputing them can manufacture a false decline.

- [ ] **Step 6: Commit**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task"
git add -A
git commit -m "feat: add weekly email flow mart with trailing engagement means"
```

---

## Task 15: Reconciliation suite and README

**Files:**

- Create: `dbt/tests/assert_revenue_reconciles_to_source.sql`
- Create: `dbt/tests/assert_no_email_join_used.sql`
- Create: `README.md`

**Interfaces:**

- Consumes: every model built so far.
- Produces: a green `dbt build` across the whole project (with the one intentional completeness warning), and a README documenting the layer.

- [ ] **Step 1: Write the reconciliation test**

Create `dbt/tests/assert_revenue_reconciles_to_source.sql`:

```sql
-- Revenue must reconcile from the raw CSVs all the way to the mart. Any
-- drift means a join is fanning out or a filter is dropping rows.
--
-- This deliberately reconciles in TWO exact hops rather than one lossy one.
--
-- Reconciling the mart's ex-VAT total directly against the source would
-- require dividing the source by 1.2, and that comparison can never be
-- exact: net_revenue is rounded to pence PER LINE, and because almost
-- every price ends in .99, those roundings are systematically upward
-- (31,514 lines round up vs 9,542 down, a net +£85.94 on £1.21m, 0.007%).
-- Per-line rounding is the correct behaviour -- it is what an invoice and
-- a tax authority actually see -- so the fix is to compare quantities that
-- SHOULD be identical, not to widen a tolerance until a lossy comparison
-- passes. A tolerance wide enough to absorb £86 would also absorb a real
-- fan-out bug.
--
-- Hop 1: source -> fct_order_line, on the untransformed VAT-inclusive
--        value. Exact, because no arithmetic has been applied yet.
-- Hop 2: fct_order_line -> mart_daily_trading, on ex-VAT net_revenue.
--        Exact, because the mart only sums what the fact already computed.

with hop1_fact as (
    select sum(net_revenue_incl_vat) as total
    from {{ ref('fct_order_line') }}
    where not is_cancelled
),

hop1_source as (
    select sum(l.price * l.quantity - l.total_discount) as total
    from {{ source('raw', 'order_lines') }} l
    inner join {{ source('raw', 'orders') }} o
        on o.id = l.order_id
    where o.cancelled_at is null
),

hop2_fact as (
    select sum(net_revenue) as total
    from {{ ref('fct_order_line') }}
    where not is_cancelled
),

hop2_mart as (
    select sum(net_revenue) as total
    from {{ ref('mart_daily_trading') }}
)

select 'hop1_source_to_fact' as hop, f.total as a, s.total as b, f.total - s.total as difference
from hop1_fact f cross join hop1_source s
where abs(f.total - s.total) > 0.01

union all

select 'hop2_fact_to_mart', m.total, f.total, m.total - f.total
from hop2_mart m cross join hop2_fact f
where abs(m.total - f.total) > 0.01
```

- [ ] **Step 2: Write the identity-safety test**

Create `dbt/tests/assert_no_email_join_used.sql`:

```sql
-- Guardrail. orders.email and customers.email differ for 86% of orders
-- (same local part, different domain). If anyone ever joins on email,
-- this test shows how catastrophic it would be: the join loses the
-- overwhelming majority of orders.
--
-- Fails if an email-based join would match MORE than 20% of orders,
-- which would mean the data changed and this guardrail needs revisiting.

with email_join as (
    select count(*) as matched
    from {{ ref('stg_orders') }} o
    inner join {{ ref('stg_customers') }} c
        on c.customer_email = o.order_email
),

id_join as (
    select count(*) as matched
    from {{ ref('stg_orders') }} o
    inner join {{ ref('stg_customers') }} c
        on c.customer_id = o.customer_id
)

select
    e.matched as email_matched,
    i.matched as id_matched
from email_join e
cross join id_join i
where e.matched > i.matched * 0.20
```

- [ ] **Step 3: Run the full build**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe build --profiles-dir .
```

Expected: every model built, every test PASS, with exactly one WARN (`assert_source_date_completeness`, 2 rows — the Meta gap).

- [ ] **Step 4: Verify a point-in-time rebuild works end to end**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task/dbt"
../venv/Scripts/dbt.exe build --profiles-dir . --vars '{as_of_date: 2025-01-31}'
../venv/Scripts/dbt.exe show --profiles-dir . --vars '{as_of_date: 2025-01-31}' --inline "select max(date_day) as latest, count(*) as n from {{ ref('mart_daily_trading') }}"
```

Expected: `latest = 2025-01-31`, and the row count is 215 × 4 = 860. This proves the whole warehouse is point-in-time correct, which the backtest in Plan 3 depends on.

Then restore the default:

```bash
../venv/Scripts/dbt.exe build --profiles-dir .
```

- [ ] **Step 5: Write `README.md`**

```markdown
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
python -m venv venv                     # Python 3.12 required; dbt does not support 3.14
venv/Scripts/pip install -r requirements.txt
cd dbt
../venv/Scripts/dbt.exe build --profiles-dir .
```

Rebuild the warehouse as of any historical date:

```bash
../venv/Scripts/dbt.exe build --profiles-dir . --vars '{as_of_date: 2025-01-31}'
```

## Data model

Three layers: `staging` (cast and rename only), `core` (star schema with
COGS and contribution margin), `marts` (the metric spine that the
detection layer reads).

See `docs/specs/2026-08-22-ecommerce-trading-engine-design.md` for the
full design and the profiling that motivated it.

## Deliberate design decisions

**Point-in-time correctness.** Every staging model filters on row
*availability* (`_weld_synced`, the ingestion timestamp) rather than event
time. The whole warehouse can be rebuilt as of any date, which is what
makes backtesting the detection layer honest.

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
`assert_no_email_join_used` is a standing guardrail.

## Known data quality issues

| Issue                 | Detail                                                      | Handling                                                                                                                                                    |
| --------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Missing ad days       | `meta_ads_daily` has no rows for 2025-03-15 or 2025-03-16 | Detected by`assert_source_date_completeness`, which warns on every build. Surfaced in `mart_data_quality` so detectors can reclassify affected signals. |
| Blank emails          | 623 customers (3.0%) have an empty email                    | `has_valid_email` and `is_marketable` on `dim_customer`                                                                                               |
| `order_count` drift | 825 customers disagree with derived counts                  | Source field counts cancelled orders;`total_spent` does not. Documented, not "fixed".                                                                     |
| Unattributed orders   | 26.9% have no usable referrer                               | Channel metrics are confidence-discounted downstream                                                                                                        |
| No TikTok cost data   | 9.0% of orders, no spend file                               | TikTok CAC is NULL by construction; blended CAC is the complete measure                                                                                     |
| Inventory is a snapshot | `products.csv` carries only CURRENT `inventory_quantity`, with no history | `mart_product_daily.days_of_cover` applies that snapshot to every historical day, so it is meaningful only at the latest date. Any backtest must read inventory signals at the run date, never historically. |

## The retention trap

Cohort retention really does collapse: the 90-day repeat rate falls
monotonically from 31.8% (2024-07 cohort) to 0.0% (2025-03 cohort), and
every one of those cohorts has full 90-day exposure. Censoring explains
only the most recent two to three cohorts, where the median 100-day gap
to a second order means the window has not closed yet.

`mart_cohort_retention` carries `has_full_exposure` and returns NULL for
`retention_rate` wherever the observation window has not elapsed.
`raw_retention_rate` is kept alongside it to show the difference.

The trap runs the other way from the obvious reading: the monthly
repeat-order rate IS stable at roughly 24% all year, but that stability
is carried by the Jul-Nov 2024 cohorts continuing to buy. Essentially no
customer acquired in 2025 returns. The blended metric looks healthy while
the cohort quality underneath it collapsed.

## What I would build next

Layer 2 (detection) and Layer 3 (recommendation and simulation) — see the
design spec.

```

- [ ] **Step 6: Freeze the dependency set**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task"
venv/Scripts/pip.exe freeze > requirements.txt
head -5 requirements.txt
```

- [ ] **Step 7: Commit**

```bash
cd "C:/Users/nguye/Downloads/DS_projects/de_task"
git add -A
git commit -m "test: add reconciliation and identity guardrails, document layer 1"
```

---

## Self-Review

**Spec coverage.** Every section 4 requirement maps to a task: staging (2–4), core star schema (5–8), metric spine (10–14), `mart_data_quality` (9), dbt tests including date-spine completeness and reconciliation (9, 15), point-in-time `as_of` plumbing (2, verified end to end in 15 step 4). Sections 5 and 6 (detection, recommendation, simulation) are Plans 2 and 3 and are deliberately out of scope here.

**Interface consistency.** `channel` uses the same four values in `stg_orders`, `dim_customer`, `mart_daily_trading` and `mart_ltv` (`meta`, `google`, `tiktok`, `unattributed`). `campaign_key` is `platform || ':' || campaign_id` in both `dim_campaign` and `fct_ad_spend_daily`. `has_full_exposure` carries the same meaning in `mart_cohort_retention` and `mart_ltv`. `contribution_margin` is defined once in `fct_order_line` and only ever summed thereafter.

**Known risks carried into execution.**

1. The `external_location` `{name}` placeholder (Task 1 step 7) is the single most likely early failure; a fallback is written into the step.
2. `to_months` and `unnest(generate_series(...))` are DuckDB-version-sensitive; alternatives are supplied inline at Tasks 5 and 12.
3. ~~Line-to-order discount reconciliation unverified.~~ Resolved before execution: verified directly against the CSVs. Line sums match `total_line_items_price` and `total_discounts` exactly across all 26,553 orders, zero variance. The same check surfaced that prices are VAT-inclusive while costs are not, which is now handled throughout (Global Constraints, Tasks 3 and 7).
4. `mart_daily_trading` and `mart_ltv` report ex-VAT revenue, so their totals will not match the VAT-inclusive figures quoted in the spec's section 2 profiling. The CAC checks in Task 10 step 5 are unaffected — CAC divides spend by customer counts, not revenue.

---

## Plans 2 and 3 (written after this plan lands)

**Plan 2 — Detection layer.** `engine/` package: `Signal` dataclass, YAML-driven detector registry, the eleven detectors, seasonal baselines, robust statistics, persistence gating, Benjamini–Hochberg FDR control, the three-way classifier, and attribution tiering. Deliverable: `python -m engine detect --as-of 2025-06-30` writes a `signals` table.

**Plan 3 — Recommendation, simulation, backtest, notebook.** Signal-to-action mapping, Monte Carlo with diminishing-returns spend response, the reversibility × confidence autonomy gate, value-of-information check, the 365-day backtest against `config/ground_truth.yml`, and the Jupyter walkthrough.
