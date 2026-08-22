{#
  Unions the two ad platforms onto one grain.

  Meta reports impressions, clicks and spend but NO conversions. Those
  columns are NULL rather than 0: coercing them to zero would make Meta's
  ROAS read as 0.0 instead of "unknown", which silently corrupts any
  cross-platform efficiency comparison.

  Google reports cost in micros (1e6 = one currency unit) and does report
  conversions, but on a different attribution basis than Shopify - it runs
  a stable ~2.0x the Shopify-attributed order count.
#}

with meta as (

    select
        'meta'                                          as platform,
        campaign_id,
        campaign_name,
        account_name,
        cast(date as date)                              as ad_date,
        cast(impressions as bigint)                     as impressions,
        cast(clicks as bigint)                          as clicks,
        cast(spend as decimal(12,2))                    as spend,
        cast(reach as bigint)                           as reach,
        cast(frequency as double)                       as frequency,
        cast(null as double)                            as platform_conversions,
        cast(null as decimal(12,2))                     as platform_conversion_value,
        try_cast(_weld_synced as timestamp)             as synced_at

    from {{ source('raw', 'meta_ads_daily') }}
    where {{ as_of_filter('_weld_synced', 'date') }}

),

google as (

    select
        'google'                                        as platform,
        campaign_id,
        campaign_name,
        account_descriptive_name                        as account_name,
        cast(date as date)                              as ad_date,
        cast(impressions as bigint)                     as impressions,
        cast(clicks as bigint)                          as clicks,
        cast(cost_micros / 1000000.0 as decimal(12,2))  as spend,
        cast(null as bigint)                            as reach,
        cast(null as double)                            as frequency,
        cast(conversions as double)                     as platform_conversions,
        cast(conversions_value as decimal(12,2))        as platform_conversion_value,
        try_cast(_weld_synced as timestamp)             as synced_at

    from {{ source('raw', 'google_ads_daily') }}
    where {{ as_of_filter('_weld_synced', 'date') }}

),

unioned as (
    select * from meta
    union all
    select * from google
)

-- Roll up to the declared grain. google_ads_daily carries device and
-- ad_network_type columns, but they do NOT fan out the grain: the file has
-- 1825 rows and 1825 distinct campaign-days (5 campaigns x 365 days), each
-- row simply tagged with one device/network label. This GROUP BY is
-- therefore a no-op on the current data. It is kept deliberately so the
-- declared grain is enforced in code rather than assumed, which is what
-- gives the uniqueness test something real to assert. Device and network
-- are dropped; no detector uses them.
select
    platform,
    campaign_id,
    max(campaign_name)                  as campaign_name,
    max(account_name)                   as account_name,
    ad_date,
    sum(impressions)                    as impressions,
    sum(clicks)                         as clicks,
    sum(spend)                          as spend,
    sum(reach)                          as reach,
    avg(frequency)                      as frequency,
    sum(platform_conversions)           as platform_conversions,
    sum(platform_conversion_value)      as platform_conversion_value,
    max(synced_at)                      as synced_at

from unioned
group by platform, campaign_id, ad_date
