with src as (
    select * from {{ source('raw', 'orders') }}
)

select
    id                                                   as order_id,
    name                                                 as order_name,
    customer_id,
    lower(trim(email))                                   as order_email,
    currency,
    cast(total_price as decimal(12,2))                   as total_price,
    cast(subtotal_price as decimal(12,2))                as subtotal_price,
    cast(total_discounts as decimal(12,2))               as total_discounts,
    cast(total_line_items_price as decimal(12,2))        as total_line_items_price,
    cast(total_tax as decimal(12,2))                     as total_tax,
    cast(total_shipping_price_set_shop_money_amount
         as decimal(12,2))                               as total_shipping,
    financial_status,
    fulfillment_status,
    cast(created_at as timestamp)                        as created_at,
    cast(created_at as date)                             as order_date,
    cast(cancelled_at as timestamp)                      as cancelled_at,
    cancelled_at is not null                             as is_cancelled,
    cancel_reason,
    buyer_accepts_marketing,
    source_name,
    referring_site,
    landing_site,
    shipping_address_country_code                        as country_code,
    shipping_address_city                                as city,
    order_number,
    {{ channel_from_referrer('referring_site') }}        as channel,
    try_cast(_weld_synced as timestamp)                  as synced_at

from src
where {{ as_of_filter('_weld_synced', 'created_at') }}
