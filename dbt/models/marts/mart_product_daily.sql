{#
  Product velocity with a dense grid.

  The grid matters: a product that sells nothing on a given day must
  produce a zero row, not a missing row, or the trailing means silently
  skip days and overstate velocity.

  days_of_cover uses current inventory against trailing 28-day velocity.
  The catalogue provides only a current snapshot of inventory, so cover
  is only meaningful on the final as_of date - flagged in the README.
#}

with grid as (
    select d.date_day, p.variant_id
    from {{ ref('dim_date') }} d
    cross join {{ ref('dim_product') }} p
),

sold as (
    select
        order_date                                      as date_day,
        variant_id,
        sum(quantity)                                   as units,
        sum(net_revenue)                                as net_revenue,
        sum(contribution_margin)                        as contribution_margin
    from {{ ref('fct_order_line') }}
    where not is_cancelled
    group by order_date, variant_id
),

dense as (
    select
        g.date_day,
        g.variant_id,
        coalesce(s.units, 0)                            as units,
        coalesce(s.net_revenue, 0)                      as net_revenue,
        coalesce(s.contribution_margin, 0)              as contribution_margin
    from grid g
    left join sold s
        on s.date_day = g.date_day and s.variant_id = g.variant_id
)

select
    d.date_day,
    d.variant_id,
    p.product_title,
    p.sku,
    p.product_type,
    d.units,
    d.net_revenue,
    d.contribution_margin,

    avg(d.units) over (
        partition by d.variant_id order by d.date_day
        rows between 6 preceding and current row
    )                                                   as velocity_7d,

    avg(d.units) over (
        partition by d.variant_id order by d.date_day
        rows between 27 preceding and current row
    )                                                   as velocity_28d,

    avg(d.units) over (
        partition by d.variant_id order by d.date_day
        rows between 6 preceding and current row
    ) / nullif(avg(d.units) over (
        partition by d.variant_id order by d.date_day
        rows between 27 preceding and current row
    ), 0)                                               as velocity_ratio,

    p.inventory_quantity,

    p.inventory_quantity / nullif(avg(d.units) over (
        partition by d.variant_id order by d.date_day
        rows between 27 preceding and current row
    ), 0)                                               as days_of_cover

from dense d
inner join {{ ref('dim_product') }} p
    on p.variant_id = d.variant_id
