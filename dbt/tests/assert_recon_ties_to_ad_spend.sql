{#
  Delivered spend in the recon mart equals the monthly sum of
  fct_ad_spend_daily (visible days only; staging applies the cursor), and
  every month with spend appears. TikTok and the unallocated bucket have no
  spend and must stay NULL.
#}

with ad as (
    select cast(date_trunc('month', ad_date) as date) as month_start,
           platform as channel,
           sum(spend) as spend
    from {{ ref('fct_ad_spend_daily') }}
    group by all
)

select
    coalesce(r.month_start, a.month_start) as month_start,
    coalesce(r.channel, a.channel)         as channel,
    r.delivered_spend,
    a.spend
from {{ ref('mart_marketing_invoice_recon') }} r
full outer join ad a
    on a.month_start = r.month_start and a.channel = r.channel
where r.delivered_spend is distinct from a.spend
