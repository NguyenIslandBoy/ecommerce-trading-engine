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

bounds as (
    -- Derived, not hardcoded: the oldest cohort could theoretically be
    -- observed for this many months by the last date with data. A fixed
    -- literal here would truncate SILENTLY (no relationship test protects
    -- it, unlike dim_date's fixed range) if the data window grows.
    select
        datediff(
            'month',
            min(cohort_month),
            (select max(date_day) from {{ ref('dim_date') }})
        ) as max_months_since
    from cohort_sizes
),

ages as (
    select unnest(generate_series(0, (select max_months_since from bounds))) as months_since
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
