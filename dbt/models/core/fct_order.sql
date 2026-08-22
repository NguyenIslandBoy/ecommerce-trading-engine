with first_order as (
    select customer_id, min(created_at) as first_created_at
    from {{ ref('stg_orders') }}
    where not is_cancelled
    group by customer_id
)

select
    o.order_id,
    o.customer_id,
    o.order_date,
    o.created_at,
    o.channel,
    o.is_cancelled,
    o.financial_status,
    o.fulfillment_status,
    o.total_price,
    o.subtotal_price,
    o.total_discounts,
    o.total_line_items_price,
    o.total_tax,
    o.total_shipping,
    o.country_code,
    o.city,
    -- A customer's acquiring order. Cancelled orders never acquire.
    (not o.is_cancelled and o.created_at = f.first_created_at) as is_first_order

from {{ ref('stg_orders') }} o
left join first_order f
    on f.customer_id = o.customer_id
