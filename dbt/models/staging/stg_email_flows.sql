{#
  Klaviyo flow engagement. Run_Date is a WEEKLY snapshot (53 distinct
  dates across the 12 months), not a daily one. Every downstream consumer
  must respect that grain.
#}

with src as (
    select * from {{ source('raw', 'email_flows') }}
)

select
    Flow_ID                                         as flow_id,
    Flow_Name                                       as flow_name,
    Message_ID                                      as message_id,
    Message_Name                                    as message_name,
    Message_Channel                                 as message_channel,
    cast(Run_Date as date)                          as week_start,
    Status                                          as flow_status,
    Message_Status                                  as message_status,
    cast(Total_Recipients as bigint)                as recipients,
    cast(Unique_Opens as bigint)                    as unique_opens,
    cast(Unique_Clicks as bigint)                   as unique_clicks,
    cast(Unique_Unsubscribes as bigint)             as unique_unsubscribes,
    cast(Unique_Placed_Order as bigint)             as unique_orders,
    cast(Total_Placed_Order as bigint)              as total_orders,
    cast(Total_Placed_Order_Value as decimal(12,2)) as order_value,
    Tags                                            as tags,
    try_cast(_weld_synced as timestamp)             as synced_at

from src
where {{ as_of_filter('_weld_synced', 'Run_Date') }}
