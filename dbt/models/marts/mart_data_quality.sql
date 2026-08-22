{#
  Completeness audit for the daily sources.

  A "gap" is a day present in the date spine but absent from the source.
  This is what catches meta_ads_daily's missing 2025-03-15 and
  2025-03-16, which a naive detector would otherwise read as spend
  collapsing to zero.
#}

with spine as (
    select date_day from {{ ref('dim_date') }}
),

orders_daily as (
    select order_date as date_day, count(*) as row_count
    from {{ ref('fct_order') }}
    group by order_date
),

meta_daily as (
    select ad_date as date_day, count(*) as row_count
    from {{ ref('fct_ad_spend_daily') }}
    where platform = 'meta'
    group by ad_date
),

google_daily as (
    select ad_date as date_day, count(*) as row_count
    from {{ ref('fct_ad_spend_daily') }}
    where platform = 'google'
    group by ad_date
),

combined as (

    select 'orders' as source_name, s.date_day, o.row_count
    from spine s left join orders_daily o on o.date_day = s.date_day

    union all

    select 'meta_ads_daily', s.date_day, m.row_count
    from spine s left join meta_daily m on m.date_day = s.date_day

    union all

    select 'google_ads_daily', s.date_day, g.row_count
    from spine s left join google_daily g on g.date_day = s.date_day

)

select
    source_name,
    date_day,
    true                                        as expected,
    row_count is not null                       as observed,
    row_count is null                           as is_gap,
    coalesce(row_count, 0)                      as row_count,
    case
        when row_count is null then 'missing_day'
        else 'ok'
    end                                         as issue_type

from combined
