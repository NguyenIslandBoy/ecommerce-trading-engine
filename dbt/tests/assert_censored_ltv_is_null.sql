-- A cohort/horizon whose observation window has not fully elapsed must not
-- report ltv_margin. If it does, the censoring guard is broken and a
-- detector reading ltv_margin unfiltered will read a less-censored cohort
-- as LTV recovering, which is precisely backwards.

select
    cohort_month,
    acquisition_channel,
    horizon_days,
    has_full_exposure,
    ltv_margin
from {{ ref('mart_ltv') }}
where not has_full_exposure
  and ltv_margin is not null
