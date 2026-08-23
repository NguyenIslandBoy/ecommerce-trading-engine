-- Setting vat_rate to 0 or dropping the divisor inflates every revenue,
-- LTV and ROAS figure by 20% while every other test still passes.
-- The ratio is 1.199915 rather than exactly 1.2 because net_revenue is
-- rounded per line; the tolerance accommodates that, not a missing divisor.
select
    sum(net_revenue_incl_vat) as incl_vat,
    sum(net_revenue)          as ex_vat,
    sum(net_revenue_incl_vat) / nullif(sum(net_revenue), 0) as ratio
from {{ ref('fct_order_line') }}
where not is_cancelled
having abs(sum(net_revenue_incl_vat) / nullif(sum(net_revenue), 0) - 1.20) > 0.001
