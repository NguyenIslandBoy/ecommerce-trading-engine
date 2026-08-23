-- A cohort whose observation window has not fully elapsed must not
-- report a retention rate. If it does, the censoring guard is broken and
-- the retention detector will fire on an artifact.

select
    cohort_month,
    months_since,
    has_full_exposure,
    retention_rate
from {{ ref('mart_cohort_retention') }}
where not has_full_exposure
  and retention_rate is not null
