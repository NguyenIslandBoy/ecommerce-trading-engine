-- The converse of assert_censored_ltv_is_null. A cohort/horizon whose
-- observation window HAS fully elapsed must report a non-NULL ltv_margin
-- AND ltv_revenue, including a legitimate low or zero value. Without this
-- test, a guard inverted to always-NULL would pass the censoring test
-- while silently erasing every real LTV signal this mart exists to surface.

select
    cohort_month,
    acquisition_channel,
    horizon_days,
    has_full_exposure,
    ltv_margin,
    ltv_revenue
from {{ ref('mart_ltv') }}
where has_full_exposure
  and (ltv_margin is null or ltv_revenue is null)
