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
