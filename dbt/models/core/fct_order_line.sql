{#
  The margin fact table.

  Ex-VAT product margins range 64.0% to 82.0%, so revenue-based and
  margin-based conclusions diverge materially. Every downstream value
  metric reads contribution_margin, not net_revenue.

  VAT: source line values are VAT-INCLUSIVE (taxes_included is true on
  every order; total_tax = subtotal_price / 6 exactly, i.e. 20% already
  inside the price). products.cost is an EX-VAT cost price. Subtracting
  one from the other directly overstates margin by 6 to 8 points per
  variant, so net_revenue divides out VAT first.

  Both figures are kept: net_revenue_incl_vat reconciles to the source
  order totals, net_revenue is the true revenue that margin is taken on.

  COGS is unit_cost x quantity. The catalogue carries a single current
  cost per variant, so this is a point-in-time cost applied historically -
  noted as an assumption in the README.
#}

select
    l.order_line_id,
    l.order_id,
    o.customer_id,
    l.variant_id,
    l.product_id,
    l.sku,
    o.order_date,
    o.channel,
    o.is_cancelled,
    l.quantity,

    -- VAT-inclusive, reconciles to orders.total_line_items_price
    cast(l.unit_price * l.quantity as decimal(12,2))         as gross_revenue_incl_vat,
    l.total_discount                                          as line_discount,
    cast(l.unit_price * l.quantity - l.total_discount
         as decimal(12,2))                                    as net_revenue_incl_vat,

    -- Ex-VAT. This is the revenue every downstream metric uses.
    cast((l.unit_price * l.quantity - l.total_discount)
         / (1 + {{ var('vat_rate') }}) as decimal(12,2))      as net_revenue,
    cast(p.unit_cost * l.quantity as decimal(12,2))          as cogs,
    cast((l.unit_price * l.quantity - l.total_discount)
         / (1 + {{ var('vat_rate') }})
         - p.unit_cost * l.quantity as decimal(12,2))         as contribution_margin

from {{ ref('stg_order_lines') }} l
inner join {{ ref('fct_order') }} o
    on o.order_id = l.order_id
inner join {{ ref('dim_product') }} p
    on p.variant_id = l.variant_id
