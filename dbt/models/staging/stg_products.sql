with src as (
    select * from {{ source('raw', 'products') }}
)

select
    variant_id,
    product_id,
    product_title,
    product_type,
    sku,
    variant_title,
    cast(price as decimal(12,2))                        as price,
    cast(price / (1 + {{ var('vat_rate') }})
         as decimal(12,2))                              as price_ex_vat,
    cast(compare_at_price as decimal(12,2))             as compare_at_price,
    cast(cost as decimal(12,2))                         as unit_cost,
    -- Margin on the EX-VAT price. Source prices include 20% VAT; cost
    -- does not. Using the inclusive price here would read 71.7% for
    -- CBD Oil 10ml against its true 66.0%.
    cast((price / (1 + {{ var('vat_rate') }}) - cost)
         / nullif(price / (1 + {{ var('vat_rate') }}), 0)
         as decimal(6,4))                               as margin_pct,
    weight_grams,
    cast(inventory_quantity as bigint)                  as inventory_quantity,
    status,
    vendor

from src
