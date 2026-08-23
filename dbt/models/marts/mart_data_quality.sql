{#
  Completeness audit for the daily sources.

  A "gap" is a day present in the date spine but absent from the source.
  This is what catches meta_ads_daily's missing 2025-03-15 and
  2025-03-16, which a naive detector would otherwise read as spend
  collapsing to zero.

  Row counts per ad source-day are rigid (meta always 6 campaigns/day,
  google always 5/day) once a day is present at all, so a day missing
  even ONE campaign is a real gap too -- one grain finer than a wholly
  missing day. `expected_count` is derived per platform as the modal
  (most common) non-zero daily row count, not hardcoded, so it tracks the
  data if the campaign roster changes. `partial_day` catches
  0 < row_count < expected_count; `missing_day` stays reserved for
  row_count = 0 (the source has no row at all for that day).
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

meta_expected as (
    select row_count, count(*) as n_days
    from meta_daily
    where row_count > 0
    group by row_count
    order by n_days desc, row_count desc
    limit 1
),

google_expected as (
    select row_count, count(*) as n_days
    from google_daily
    where row_count > 0
    group by row_count
    order by n_days desc, row_count desc
    limit 1
),

combined as (

    select 'orders' as source_name, s.date_day, o.row_count, cast(null as bigint) as expected_count
    from spine s left join orders_daily o on o.date_day = s.date_day

    union all

    select 'meta_ads_daily', s.date_day, m.row_count, me.row_count as expected_count
    from spine s
    left join meta_daily m on m.date_day = s.date_day
    cross join meta_expected me

    union all

    select 'google_ads_daily', s.date_day, g.row_count, ge.row_count as expected_count
    from spine s
    left join google_daily g on g.date_day = s.date_day
    cross join google_expected ge

)

select
    source_name,
    date_day,
    true                                        as expected,
    row_count is not null                       as observed,
    row_count is null                           as is_gap,
    coalesce(row_count, 0)                      as row_count,
    expected_count,
    case
        when row_count is null then 'missing_day'
        when expected_count is not null
             and coalesce(row_count, 0) < expected_count then 'partial_day'
        else 'ok'
    end                                         as issue_type

from combined
