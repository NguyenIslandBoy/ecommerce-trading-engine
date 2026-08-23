{#
  The primary metric spine.

  Spend maps to a channel by platform: meta -> 'meta', google -> 'google'.
  TikTok drives 9% of orders but has NO cost file, so its CAC is
  structurally uncomputable and stays NULL. Unattributed (26.9% of
  orders) has no spend by definition.

  blended_cac = total ad spend / total new customers for the day, repeated
  on every channel row. It is the only complete cost measure and is the
  attribution-free cross-check on channel_cac.
#}

with order_facts as (

    select
        o.order_date                                    as date_day,
        o.channel,
        count(distinct o.order_id)                      as orders,
        count(distinct case when o.is_first_order
                            then o.customer_id end)     as new_customers,
        count(distinct case when not o.is_first_order
                            then o.customer_id end)     as returning_customers
    from {{ ref('fct_order') }} o
    where not o.is_cancelled
    group by o.order_date, o.channel

),

line_facts as (

    select
        order_date                                      as date_day,
        channel,
        sum(net_revenue)                                as net_revenue,
        sum(contribution_margin)                        as contribution_margin,
        -- Basket size and discount intensity, so AOV movement can be
        -- decomposed rather than merely trended: a falling AOV caused by
        -- smaller baskets is a different problem from one caused by
        -- discounting, and they call for opposite actions.
        sum(quantity)                                   as units,
        sum(line_discount)                              as discounts
    from {{ ref('fct_order_line') }}
    where not is_cancelled
    group by order_date, channel

),

spend_facts as (

    select
        ad_date                                         as date_day,
        platform                                        as channel,
        sum(spend)                                      as ad_spend,
        sum(clicks)                                     as clicks,
        sum(impressions)                                as impressions,
        max(available_on)                               as spend_available_on
    from {{ ref('fct_ad_spend_daily') }}
    group by ad_date, platform

),

daily_totals as (

    select
        date_day,
        sum(new_customers)                              as total_new_customers
    from order_facts
    group by date_day

),

daily_spend as (

    select
        date_day,
        sum(ad_spend)                                   as total_ad_spend
    from spend_facts
    group by date_day

),

ad_spend_completeness as (

    -- blended_cac sums whatever spend rows exist for the day, so a source
    -- with a missing day (meta_ads_daily on 2025-03-15/16) OR a day
    -- missing just one campaign's rows (partial_day) silently reads as a
    -- partial total rather than an incomplete one. This flag makes that
    -- absence explicit -- on either issue_type -- so blended_cac can be
    -- NULLed instead of fabricating a CAC improvement out of missing spend.
    select
        date_day,
        not bool_or(issue_type != 'ok')                 as ad_spend_is_complete
    from {{ ref('mart_data_quality') }}
    where source_name in ('meta_ads_daily', 'google_ads_daily')
    group by date_day

),

grid as (
    select d.date_day, c.channel
    from {{ ref('dim_date') }} d
    cross join (select unnest(['meta','google','tiktok','unattributed']) as channel) c
)

select
    g.date_day,
    g.channel,
    coalesce(o.orders, 0)                               as orders,
    coalesce(o.new_customers, 0)                        as new_customers,
    coalesce(o.returning_customers, 0)                  as returning_customers,
    coalesce(l.net_revenue, 0)                          as net_revenue,
    coalesce(l.contribution_margin, 0)                  as contribution_margin,
    cast(l.net_revenue / nullif(o.orders, 0)
         as decimal(12,2))                              as aov,
    coalesce(l.units, 0)                                as units,
    coalesce(l.discounts, 0)                            as discounts,
    -- AOV = units_per_order * revenue_per_unit. Kept as columns so a detector
    -- reads the decomposition instead of re-deriving it.
    cast(l.units * 1.0 / nullif(o.orders, 0)
         as decimal(12,4))                              as units_per_order,
    cast(l.net_revenue / nullif(l.units, 0)
         as decimal(12,4))                              as revenue_per_unit,
    cast(l.discounts / nullif(l.net_revenue + l.discounts, 0)
         as decimal(12,6))                              as discount_rate,

    s.ad_spend,
    s.clicks,
    s.impressions,

    -- Date the spend figures on this row became visible. Ads sync one day
    -- after event date, so at cursor D this row's spend is knowable only
    -- when spend_available_on <= D. The backtest reads this instead of
    -- re-deriving the lag, and instead of rebuilding the warehouse 365 times.
    s.spend_available_on,

    -- Tier C: depends on last-click attribution.
    cast(s.ad_spend / nullif(o.new_customers, 0)
         as decimal(12,2))                              as channel_cac,
    cast(l.net_revenue / nullif(s.ad_spend, 0)
         as decimal(12,4))                              as channel_roas,
    cast(l.contribution_margin / nullif(s.ad_spend, 0)
         as decimal(12,4))                              as channel_margin_roas,

    -- Tier B: attribution-free, identical across a day's channel rows.
    -- NULL whenever any ad source is missing a day's rows: a partial spend
    -- total divided by the full new-customer count reads as a fabricated
    -- CAC improvement, not as "unknown".
    coalesce(ac.ad_spend_is_complete, false)            as ad_spend_is_complete,
    case when coalesce(ac.ad_spend_is_complete, false)
        then cast(ds.total_ad_spend / nullif(dt.total_new_customers, 0)
                  as decimal(12,2))
    end                                                  as blended_cac,

    d.is_peak_season,
    d.iso_dow,
    d.week_start

from grid g
left join order_facts  o  on o.date_day = g.date_day and o.channel = g.channel
left join line_facts   l  on l.date_day = g.date_day and l.channel = g.channel
left join spend_facts  s  on s.date_day = g.date_day and s.channel = g.channel
left join daily_totals dt on dt.date_day = g.date_day
left join daily_spend  ds on ds.date_day = g.date_day
left join ad_spend_completeness ac on ac.date_day = g.date_day
inner join {{ ref('dim_date') }} d on d.date_day = g.date_day
