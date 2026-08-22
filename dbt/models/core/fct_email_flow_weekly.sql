{#
  Engagement rates AND conversion rate sit side by side deliberately.

  The email decay detector compares their trends: if opens fall while
  conversion holds, the flows are still monetising and the decay is a
  measurement or deliverability artifact, not lost demand.
#}

select
    flow_id,
    flow_name,
    message_id,
    message_name,
    week_start,
    flow_status,
    recipients,
    unique_opens,
    unique_clicks,
    unique_unsubscribes,
    unique_orders,
    total_orders,
    order_value,

    cast(unique_opens  * 1.0 / nullif(recipients, 0) as decimal(8,6)) as open_rate,
    cast(unique_clicks * 1.0 / nullif(recipients, 0) as decimal(8,6)) as click_rate,
    cast(unique_orders * 1.0 / nullif(recipients, 0) as decimal(8,6)) as conversion_rate,
    cast(unique_unsubscribes * 1.0 / nullif(recipients, 0)
         as decimal(8,6))                                             as unsubscribe_rate,
    cast(order_value / nullif(recipients, 0) as decimal(12,4))        as revenue_per_recipient

from {{ ref('stg_email_flows') }}
