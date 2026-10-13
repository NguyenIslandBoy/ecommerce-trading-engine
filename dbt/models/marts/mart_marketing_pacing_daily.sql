{#
  SYNTHETIC budgets against delivered spend. Grain: date x channel, every
  calendar day of every month in the date spine (the whole month, even when
  the cursor falls mid-month, so the plan always runs to month-end).

  Plan. planned_cumulative = ROUND(approved_budget x cumulative weight, 2),
  weight from the pacing_cumulative_weight macro (linear or front_loaded).
  planned_spend is the day-on-day difference, so a month's plan sums to its
  budget to the penny. The budget is the latest version visible at the
  cursor.

  Delivered. A day's spend is visible only from its spend_available_on
  (one day after the trading day); staging has already applied the cursor.
  spend_status says why a day has no spend:
    delivered        spend is visible
    not_yet_visible  the day's spend has not landed at the cursor
    missing          the day is past, but the source never delivered it
                     (meta 2025-03-15/16); the cumulative carries flat
    no_cost_source   the channel has no cost file (tiktok)
  delivered_cumulative is NULL, never 0, on not_yet_visible and
  no_cost_source rows.

  pacing_ratio = delivered_cumulative / planned_cumulative: a ratio of
  sums, never an average of daily ratios.

  Forecast. A SIMPLE RUN-RATE FORECAST, not a model:
    run_rate_7d        mean daily spend over the last 7 calendar days up to
                       this day, over the days with spend
    forecast_month_end delivered_cumulative + run_rate_7d x days remaining
    forecast_overspend forecast_month_end - approved_budget (positive:
                       forecast to overspend)
  It ignores seasonality and any planned change in spend.
#}

with months as (

    select distinct month_start from {{ ref('dim_date') }}

),

calendar as (

    select
        cast(unnest(generate_series(m.month_start, last_day(m.month_start), interval 1 day)) as date)
                                                            as date_day,
        m.month_start
    from months m

),

channels as (

    select * from (values ('meta', true), ('google', true), ('tiktok', false))
        as t(channel, has_cost_source)

),

budget as (

    select
        budget_month                                        as month_start,
        channel,
        approved_amount,
        budget_version,
        pacing_profile,
        row_number() over (
            partition by budget_month, channel
            order by approved_on desc,
                     case budget_version when 'revised' then 0 else 1 end
        )                                                   as recency
    from {{ ref('stg_marketing_budget') }}

),

spend as (

    select ad_date as date_day, platform as channel, sum(spend) as spend
    from {{ ref('fct_ad_spend_daily') }}
    group by all

),

base as (

    select
        c.date_day,
        c.month_start,
        ch.channel,
        ch.has_cost_source,
        extract(day from c.date_day)                        as day_of_month,
        extract(day from last_day(c.month_start))           as days_in_month,
        b.approved_amount                                   as approved_budget,
        b.budget_version                                    as budget_version_used,
        b.pacing_profile,
        s.spend                                             as delivered_spend,
        case
            when not ch.has_cost_source                     then 'no_cost_source'
            when s.spend is not null                        then 'delivered'
            -- spend lands the day after the trading day
            when c.date_day + 1 > cast('{{ var("as_of_date") }}' as date)
                                                            then 'not_yet_visible'
            else 'missing'
        end                                                 as spend_status
    from calendar c
    cross join channels ch
    left join budget b
        on b.month_start = c.month_start and b.channel = ch.channel and b.recency = 1
    left join spend s
        on s.date_day = c.date_day and s.channel = ch.channel

),

planned as (

    select
        *,
        cast(round(approved_budget * {{ pacing_cumulative_weight('pacing_profile', 'day_of_month', 'days_in_month') }}, 2)
             as decimal(12,2))                              as planned_cumulative
    from base

),

delivered as (

    select
        *,
        sum(delivered_spend) over (
            partition by channel, month_start order by date_day
            rows between unbounded preceding and current row
        )                                                   as running_delivered,
        avg(delivered_spend) over (
            partition by channel order by date_day
            rows between 6 preceding and current row
        )                                                   as trailing_mean
    from planned

)

select
    date_day,
    month_start,
    channel,
    day_of_month,
    days_in_month,
    approved_budget,
    budget_version_used,
    pacing_profile,
    cast(planned_cumulative - coalesce(lag(planned_cumulative) over (
            partition by channel, month_start order by date_day), 0)
         as decimal(12,2))                                  as planned_spend,
    planned_cumulative,
    cast(delivered_spend as decimal(12,2))                  as delivered_spend,
    cast(case when spend_status in ('delivered', 'missing') then running_delivered end
         as decimal(12,2))                                  as delivered_cumulative,
    spend_status,
    cast(case when spend_status in ('delivered', 'missing')
              then running_delivered / nullif(planned_cumulative, 0) end
         as decimal(12,6))                                  as pacing_ratio,
    cast(case when spend_status in ('delivered', 'missing') then trailing_mean end
         as decimal(12,2))                                  as run_rate_7d,
    cast(case when spend_status in ('delivered', 'missing')
              then running_delivered + trailing_mean * (days_in_month - day_of_month) end
         as decimal(12,2))                                  as forecast_month_end,
    cast(case when spend_status in ('delivered', 'missing')
              then running_delivered + trailing_mean * (days_in_month - day_of_month)
                   - approved_budget end
         as decimal(12,2))                                  as forecast_overspend,
    true                                                    as is_synthetic_finance_input,
    -- The cursor this build ran at, so an export can say what it knew when.
    cast('{{ var("as_of_date") }}' as date)                as as_of_date

from delivered
