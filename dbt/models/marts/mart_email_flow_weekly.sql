{#
  Flow-level weekly engagement.

  GRAIN WARNING: the source's `Flow_ID` is NOT a flow identifier -- it is a
  MESSAGE identifier. "Welcome Series" spans FL001, FL002 and FL003; there are
  12 Flow_IDs across only 6 real flows, and exactly one Message_ID per Flow_ID.
  Grouping on flow_id therefore yields 12 x 53 = 636 rows, which is the
  message grain the fact table already has, not a rollup at all -- and
  "Welcome Series open rate" would silently become three separate series.
  The real flow identity is `flow_name`, so that is the grain here: 6 x 53 = 318.

  Rates are recomputed from summed numerators and denominators rather
  than averaged from message-level rates: averaging rates across messages
  with very different recipient counts weights a 50-recipient message
  equally with a 2,000-recipient one.
#}

with rolled as (

    select
        flow_name,
        week_start,
        sum(recipients)                                 as recipients,
        sum(unique_opens)                               as unique_opens,
        sum(unique_clicks)                              as unique_clicks,
        sum(unique_unsubscribes)                        as unique_unsubscribes,
        sum(unique_orders)                              as unique_orders,
        sum(order_value)                                as order_value
    from {{ ref('fct_email_flow_weekly') }}
    group by flow_name, week_start

),

rated as (

    select
        *,
        unique_opens  * 1.0 / nullif(recipients, 0)     as open_rate,
        unique_clicks * 1.0 / nullif(recipients, 0)     as click_rate,
        unique_orders * 1.0 / nullif(recipients, 0)     as conversion_rate,
        unique_unsubscribes * 1.0 / nullif(recipients, 0) as unsubscribe_rate,
        order_value / nullif(recipients, 0)             as revenue_per_recipient
    from rolled

)

select
    *,
    avg(open_rate) over (
        partition by flow_name order by week_start
        rows between 7 preceding and current row
    )                                                   as open_rate_8w,

    avg(click_rate) over (
        partition by flow_name order by week_start
        rows between 7 preceding and current row
    )                                                   as click_rate_8w,

    avg(conversion_rate) over (
        partition by flow_name order by week_start
        rows between 7 preceding and current row
    )                                                   as conversion_rate_8w

from rated
