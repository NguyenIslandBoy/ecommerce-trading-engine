{{ config(severity='warn') }}

-- Every daily source must have a row for every day in the spine.
--
-- THIS TEST IS EXPECTED TO FAIL ON FIRST RUN. meta_ads_daily is missing
-- 2025-03-15 and 2025-03-16. That failure is the point: it proves the
-- gap is detected mechanically rather than noticed by eye. Once
-- confirmed, it is configured to warn rather than error (Step 6) so the
-- rest of the suite stays green while the gap remains visible.

select
    source_name,
    date_day
from {{ ref('mart_data_quality') }}
where is_gap
