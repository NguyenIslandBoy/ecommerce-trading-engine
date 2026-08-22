{#
  Derived efficiency metrics live here, not in the marts, because the
  CPC decomposition detector needs CPM and CTR at campaign-day grain.

  CPC = CPM / (1000 * CTR) is an identity, so a CPC movement can always
  be attributed to its CPM component (auction price) and its CTR
  component (creative relevance). That decomposition is what turns
  "CAC is up" into an actionable recommendation.
#}

select
    a.platform || ':' || a.campaign_id                  as campaign_key,
    a.platform,
    a.campaign_id,
    c.funnel_stage,
    a.ad_date,
    a.impressions,
    a.clicks,
    a.spend,
    a.reach,
    a.frequency,
    a.platform_conversions,
    a.platform_conversion_value,

    cast(a.spend / nullif(a.clicks, 0) as decimal(12,4))            as cpc,
    cast(1000.0 * a.spend / nullif(a.impressions, 0)
         as decimal(12,4))                                          as cpm,
    cast(a.clicks * 1.0 / nullif(a.impressions, 0)
         as decimal(8,6))                                           as ctr

from {{ ref('stg_ads_daily') }} a
inner join {{ ref('dim_campaign') }} c
    on c.campaign_key = a.platform || ':' || a.campaign_id
