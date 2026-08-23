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

  repeat_rate_90d is a SEPARATE metric from retention_rate and answers a
  different question. retention_rate is calendar-month bucketed: a
  cohort's M+3 value is activity that happened in that specific calendar
  month, K months after acquisition. repeat_rate_90d is a rolling window
  measured from each customer's OWN first order date: it asks whether the
  customer placed a further (strictly later) order within 90 days of
  their first order, regardless of which calendar month that fell in. It
  is this rolling number, not the calendar-bucketed one, that the
  headline "31.8% -> 0.0%" claim in the README/design doc refers to.
  has_full_90d_exposure is the same idea as has_full_exposure but keyed
  off the LATEST first_order_date within the cohort (the worst case: the
  last customer to join has the least time to repeat) plus 90 days,
  compared against the last date with data.
#}

with customers as (

    select
        customer_id,
        first_order_date,
        first_order_month                               as cohort_month
    from {{ ref('dim_customer') }}
    where first_order_date is not null

),

cohort_sizes as (
    select cohort_month, count(*) as cohort_size
    from customers
    group by cohort_month
),

-- Rolling 90-day repeat rate: a customer counts here if they placed a
-- further (strictly later) non-cancelled order within 90 days of their
-- OWN first order date. Distinct from the `orders`/`activity` CTEs below,
-- which bucket by calendar month for retention_rate.
repeat_orders_90d as (

    select distinct
        c.customer_id,
        c.cohort_month
    from customers c
    inner join {{ ref('fct_order') }} o
        on o.customer_id = c.customer_id
       and not o.is_cancelled
       and o.order_date > c.first_order_date
       and o.order_date <= c.first_order_date + 90

),

repeat_90d_by_cohort as (
    select cohort_month, count(distinct customer_id) as repeat_within_90d
    from repeat_orders_90d
    group by cohort_month
),

exposure_90d as (
    -- Worst case within the cohort: the last customer to join has the
    -- least time to repeat, so exposure is judged off the LATEST
    -- first_order_date in the cohort, not the cohort boundary itself.
    select
        cohort_month,
        max(first_order_date) + 90 <= (select max(date_day) from {{ ref('dim_date') }})
                                                         as has_full_90d_exposure
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
    e.cohort_month,
    e.months_since,
    e.cohort_size,
    e.active_customers                                  as repeat_customers,
    e.window_end,
    e.window_end <= (select max(date_day) from {{ ref('dim_date') }})  as has_full_exposure,

    -- The guarded metric. NULL where the window has not elapsed.
    case
        when e.window_end <= (select max(date_day) from {{ ref('dim_date') }})
        then e.active_customers * 1.0 / nullif(e.cohort_size, 0)
    end                                                 as retention_rate,

    -- The unguarded metric, retained ONLY to demonstrate the artifact.
    -- Never consume this in a detector.
    e.active_customers * 1.0 / nullif(e.cohort_size, 0) as raw_retention_rate,

    -- Rolling 90-day repeat rate. Cohort-level: identical across all
    -- months_since rows for a given cohort_month. See header comment.
    coalesce(r90.repeat_within_90d, 0)                  as repeat_within_90d,
    exp90.has_full_90d_exposure,
    case
        when exp90.has_full_90d_exposure
        then coalesce(r90.repeat_within_90d, 0) * 1.0 / nullif(e.cohort_size, 0)
    end                                                 as repeat_rate_90d

from exposure e
left join repeat_90d_by_cohort r90 on r90.cohort_month = e.cohort_month
left join exposure_90d exp90 on exp90.cohort_month = e.cohort_month
where e.months_since > 0
