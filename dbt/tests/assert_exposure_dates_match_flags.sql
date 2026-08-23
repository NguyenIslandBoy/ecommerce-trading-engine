{#
  The backtest reconstructs any cursor by comparing an exposure DATE against
  that cursor, instead of rebuilding the warehouse 365 times. That only holds
  if each published flag is exactly its date compared against the last date
  with data. This test is the contract between the two.

  If it fails, the fast backtest path has silently diverged from what dbt
  would actually produce at a cursor, and every precision/recall figure
  downstream is measuring the wrong thing.
#}

with last_date as (
    select max(date_day) as d from {{ ref('dim_date') }}
),

retention_mismatch as (
    select 'mart_cohort_retention.has_full_exposure' as which, count(*) as n
    from {{ ref('mart_cohort_retention') }}, last_date
    where has_full_exposure != (window_end <= last_date.d)
),

retention_90d_mismatch as (
    select 'mart_cohort_retention.has_full_90d_exposure' as which, count(*) as n
    from {{ ref('mart_cohort_retention') }}, last_date
    where has_full_90d_exposure != (exposure_90d_end <= last_date.d)
),

ltv_mismatch as (
    select 'mart_ltv.has_full_exposure' as which, count(*) as n
    from {{ ref('mart_ltv') }}, last_date
    where has_full_exposure != (exposure_end <= last_date.d)
)

select * from retention_mismatch     where n > 0
union all
select * from retention_90d_mismatch where n > 0
union all
select * from ltv_mismatch           where n > 0
