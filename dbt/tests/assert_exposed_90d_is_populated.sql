-- The converse of assert_censored_90d_is_null.
--
-- A cohort whose 90-day observation window HAS fully elapsed must report
-- a repeat_rate_90d -- including a legitimate 0.0, which is an observed
-- fact and not a missing value. Without this test, a guard inverted to
-- always-NULL would pass the censoring test while silently erasing every
-- real signal this metric exists to surface.

select
    cohort_month,
    months_since,
    has_full_90d_exposure,
    repeat_rate_90d
from {{ ref('mart_cohort_retention') }}
where has_full_90d_exposure
  and repeat_rate_90d is null
