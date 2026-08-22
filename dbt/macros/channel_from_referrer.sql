{#
  Last-click channel from the Shopify referring_site.

  This is the only attribution available: landing_site carries no UTM
  parameters anywhere in the dataset. Blank referrers and the literal
  string 'direct' both map to 'unattributed' (26.9% of orders combined),
  which is why channel-attributed metrics are confidence-discounted
  downstream.
#}
{% macro channel_from_referrer(col) %}
    case
        when lower(coalesce({{ col }}, '')) like '%facebook%'  then 'meta'
        when lower(coalesce({{ col }}, '')) like '%instagram%' then 'meta'
        when lower(coalesce({{ col }}, '')) like '%google%'    then 'google'
        when lower(coalesce({{ col }}, '')) like '%youtube%'   then 'google'
        when lower(coalesce({{ col }}, '')) like '%tiktok%'    then 'tiktok'
        else 'unattributed'
    end
{% endmacro %}
