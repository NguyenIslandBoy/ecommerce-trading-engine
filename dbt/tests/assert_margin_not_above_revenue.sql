-- Contribution margin can never exceed net revenue. If it does, COGS has
-- been joined wrongly or a discount has been double-counted.
select
    order_line_id,
    net_revenue,
    contribution_margin
from {{ ref('fct_order_line') }}
where contribution_margin > net_revenue
