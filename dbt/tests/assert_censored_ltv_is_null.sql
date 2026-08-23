-- A cohort/horizon whose observation window has not fully elapsed must not
-- report ltv_margin or ltv_revenue. If it does, the censoring guard is
-- broken and a detector reading either column unfiltered will read a
-- less-censored cohort as LTV recovering, which is precisely backwards.

select
    cohort_month,
    acquisition_channel,
    horizon_days,
    has_full_exposure,
    ltv_margin,
    ltv_revenue
from {{ ref('mart_ltv') }}
where not has_full_exposure
  and (ltv_margin is not null or ltv_revenue is not null)
