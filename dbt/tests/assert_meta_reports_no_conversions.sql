-- Meta reports NO conversion data at all. Its conversion columns must be
-- NULL, never 0: zero means "measured, and it was none", NULL means
-- "not measured". Coercing to zero makes Meta's ROAS read as 0.0 rather
-- than unknown and silently corrupts every cross-platform comparison
-- the engine makes downstream.
--
-- Guards the invariant in BOTH directions, because a union that silently
-- dropped Google's conversions would be just as wrong.

select
    'meta_has_conversion_data' as violation,
    platform,
    campaign_id,
    ad_date
from {{ ref('stg_ads_daily') }}
where platform = 'meta'
  and (platform_conversions is not null or platform_conversion_value is not null)

union all

select
    'google_missing_conversion_data' as violation,
    platform,
    campaign_id,
    ad_date
from {{ ref('stg_ads_daily') }}
where platform = 'google'
  and (platform_conversions is null or platform_conversion_value is null)
