select
    variant_id,
    product_id,
    product_title,
    product_type,
    sku,
    variant_title,
    price,
    compare_at_price,
    unit_cost,
    margin_pct,
    weight_grams,
    inventory_quantity,
    status,
    vendor

from {{ ref('stg_products') }}
