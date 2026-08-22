with src as (
    select * from {{ source('raw', 'order_lines') }}
)

select
    src.id                                          as order_line_id,
    src.order_id,
    src."index"                                     as line_index,
    src.sku,
    src.title                                       as product_title,
    src.variant_title,
    src.vendor,
    src.product_id,
    src.variant_id,
    cast(src.quantity as bigint)                    as quantity,
    cast(src.price as decimal(12,2))                as unit_price,
    cast(src.total_discount as decimal(12,2))       as total_discount,
    src.grams,
    src.fulfillment_status

from src
-- Availability is inherited from the parent order: a line cannot be visible
-- before its order is. This keeps the as_of cursor consistent across the join.
inner join {{ ref('stg_orders') }} o
    on o.order_id = src.order_id
