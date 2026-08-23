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

),

exposure as (

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
        coalesce(w.cum_contribution_margin, 0)              as cum_contribution_margin

    from cohort_sizes s
    cross join horizons h
    left join spend_within w
        on w.cohort_month = s.cohort_month
       and w.acquisition_channel = s.acquisition_channel
       and w.horizon_days = h.horizon_days

)

select
    cohort_month,
    acquisition_channel,
    horizon_days,
    cohort_size,
    has_full_exposure,

    -- Raw sums stay populated even when censored, mirroring
    -- mart_cohort_retention's raw_retention_rate.
    cum_net_revenue,
    cum_contribution_margin,

    -- Censored cohorts publish NULL, not a partial-window figure that looks
    -- like a completed measurement. Left unguarded, a less-censored cohort
    -- can read ABOVE the last fully-exposed one (fewer high spenders have
    -- had time to churn out of the average yet), which inverts the
    -- headline LTV finding into an apparent recovery.
    case when has_full_exposure
        then cum_net_revenue * 1.0 / nullif(cohort_size, 0)
    end                                                  as ltv_revenue,

    case when has_full_exposure
        then cum_contribution_margin * 1.0 / nullif(cohort_size, 0)
    end                                                  as ltv_margin

from exposure
