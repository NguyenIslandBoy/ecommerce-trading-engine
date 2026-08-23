{#
  Pins the blended contribution-margin rate. margin_pct is the single
  most load-bearing constant in the engine -- every LTV and LTV/CAC
  figure in the warehouse scales with it -- and nothing tests it directly:
  stg_products.margin_pct's accepted_range of 0-1 lets a wrong unit_cost
  (unit error, currency, stale refresh) through so long as the resulting
  ratio still lands somewhere in [0,1].

  Verified blended rate (contribution margin / net revenue, non-cancelled):
  0.7063. +/-0.005 tolerance.
#}

select
    sum(contribution_margin) / nullif(sum(net_revenue), 0) as blended_margin_rate
from {{ ref('fct_order_line') }}
where not is_cancelled
having abs(sum(contribution_margin) / nullif(sum(net_revenue), 0) - 0.706) > 0.005
