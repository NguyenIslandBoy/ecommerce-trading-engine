-- Ad spend must reconcile from staging through the fact table to the mart,
-- the same discipline assert_revenue_reconciles_to_source applies to
-- revenue -- despite CAC being the most-quoted metric in the project, there
-- was no spend equivalent. Tolerance £0.01. Current total is £247,493.29.
--
-- Hop 1: stg_ads_daily -> fct_ad_spend_daily. Exact: the fact table only
--         adds derived efficiency columns; spend passes through unchanged.
-- Hop 2: fct_ad_spend_daily -> mart_daily_trading. Exact: the mart sums
--         spend by date and channel with no filtering.

with hop1_staging as (
    select sum(spend) as total
    from {{ ref('stg_ads_daily') }}
),

hop1_fact as (
    select sum(spend) as total
    from {{ ref('fct_ad_spend_daily') }}
),

hop2_mart as (
    select sum(ad_spend) as total
    from {{ ref('mart_daily_trading') }}
)

select 'hop1_staging_to_fact' as hop, f.total as a, s.total as b, f.total - s.total as difference
from hop1_fact f cross join hop1_staging s
where abs(f.total - s.total) > 0.01

union all

select 'hop2_fact_to_mart', m.total, f.total, m.total - f.total
from hop2_mart m cross join hop1_fact f
where abs(m.total - f.total) > 0.01
