{#
  Funnel stage is parsed from the campaign name. Both platforms use a
  "Stage - Detail" naming convention, which makes stage a reliable
  dimension for rolling spend up by intent rather than by campaign.
#}

select distinct
    platform || ':' || campaign_id       as campaign_key,
    platform,
    campaign_id,
    campaign_name,
    case
        when lower(campaign_name) like 'prospecting%'  then 'prospecting'
        when lower(campaign_name) like 'retargeting%'  then 'retargeting'
        when lower(campaign_name) like 'dpa%'          then 'catalogue'
        when lower(campaign_name) like 'brand%'        then 'brand'
        when lower(campaign_name) like 'non-brand%'    then 'non_brand'
        when lower(campaign_name) like 'shopping%'     then 'shopping'
        when lower(campaign_name) like 'pmax%'         then 'pmax'
        else 'other'
    end                                  as funnel_stage

from {{ ref('stg_ads_daily') }}
