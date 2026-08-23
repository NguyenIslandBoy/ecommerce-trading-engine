-- A cohort whose 90-day observation window has not fully elapsed must
-- not report a repeat_rate_90d. If it does, the censoring guard is
-- broken and the rolling repeat-rate figure will surface an artifact.

select
    cohort_month,
    months_since,
    has_full_90d_exposure,
    repeat_rate_90d
from {{ ref('mart_cohort_retention') }}
where not has_full_90d_exposure
  and repeat_rate_90d is not null
