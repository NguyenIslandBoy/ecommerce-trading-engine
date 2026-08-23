-- contribution_margin must equal net_revenue - cogs exactly. Deriving the
-- ex-VAT value twice (once per column) lets them round independently, which
-- silently publishes three columns that contradict each other.
select order_line_id, net_revenue, cogs, contribution_margin
from {{ ref('fct_order_line') }}
where abs(contribution_margin - (net_revenue - cogs)) > 0.001
