{#
  Only mart_daily_trading is reconciled to fct_order_line elsewhere.
  mart_product_daily and mart_ltv each carry their own `not is_cancelled`
  filter with nothing checking it -- dropping it from either inflates that
  mart's money by 3.14% (cancelled lines = 1,302 rows, £39,269.92 net
  revenue) while every other test node stays green.

  mart_product_daily sums every sold line with no windowing, so it ties
  EXACTLY to fct_order_line's full non-cancelled total.

  mart_ltv's money is per cohort x channel x HORIZON; summing across all
  three horizons would triple-count a line that falls inside more than one
  horizon's window. Reconciling a single horizon (90, the longest, per
  the review) against fct_order_line's FULL total does not work either:
  horizon=90 only captures revenue within 90 days of each customer's
  first order, and this project's own retention-collapse finding means
  real revenue exists well past that window for the early cohorts (median
  gap to a 2nd order is 100 days). Checked before writing this test:
  summing cum_net_revenue at horizon=90 across all cohorts is
  £1,036,988.88 -- genuinely £174,701.00 short of fct_order_line's full
  non-cancelled total of £1,211,689.88 (cum_contribution_margin is
  £123,418.20 short on the same basis). That gap is real, not a bug, so
  widening the tolerance to swallow it would hide an actual regression.

  Instead mart_ltv is reconciled against the SAME windowed slice of
  fct_order_line the mart itself is built from (join to dim_customer,
  keep only lines with order_date in [first_order_date, first_order_date
  + 90), non-cancelled), recomputed independently here. That ties to the
  penny (verified) and still catches a dropped `not is_cancelled` filter,
  which is the regression this test exists to guard.
#}

with mart_product_daily_totals as (
    select
        sum(net_revenue)          as net_revenue,
        sum(contribution_margin)  as contribution_margin
    from {{ ref('mart_product_daily') }}
),

mart_ltv_90 as (
    select
        sum(cum_net_revenue)          as net_revenue,
        sum(cum_contribution_margin)  as contribution_margin
    from {{ ref('mart_ltv') }}
    where horizon_days = 90
),

fct_totals as (
    select
        sum(net_revenue)          as net_revenue,
        sum(contribution_margin)  as contribution_margin
    from {{ ref('fct_order_line') }}
    where not is_cancelled
),

fct_windowed_90 as (
    select
        sum(l.net_revenue)          as net_revenue,
        sum(l.contribution_margin)  as contribution_margin
    from {{ ref('fct_order_line') }} l
    inner join {{ ref('dim_customer') }} c
        on c.customer_id = l.customer_id
       and c.first_order_date is not null
    where not l.is_cancelled
      and l.order_date >= c.first_order_date
      and l.order_date <  c.first_order_date + 90
)

select 'mart_product_daily.net_revenue' as check, m.net_revenue as mart_value, f.net_revenue as reconciled_value, m.net_revenue - f.net_revenue as difference
from mart_product_daily_totals m cross join fct_totals f
where abs(m.net_revenue - f.net_revenue) > 0.01

union all

select 'mart_product_daily.contribution_margin', m.contribution_margin, f.contribution_margin, m.contribution_margin - f.contribution_margin
from mart_product_daily_totals m cross join fct_totals f
where abs(m.contribution_margin - f.contribution_margin) > 0.01

union all

select 'mart_ltv.cum_net_revenue (horizon=90)', l.net_revenue, w.net_revenue, l.net_revenue - w.net_revenue
from mart_ltv_90 l cross join fct_windowed_90 w
where abs(l.net_revenue - w.net_revenue) > 0.01

union all

select 'mart_ltv.cum_contribution_margin (horizon=90)', l.contribution_margin, w.contribution_margin, l.contribution_margin - w.contribution_margin
from mart_ltv_90 l cross join fct_windowed_90 w
where abs(l.contribution_margin - w.contribution_margin) > 0.01
