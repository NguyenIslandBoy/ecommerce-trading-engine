-- Revenue must reconcile from the raw CSVs all the way to the mart. Any
-- drift means a join is fanning out or a filter is dropping rows.
--
-- This deliberately reconciles in TWO exact hops rather than one lossy one.
--
-- Reconciling the mart's ex-VAT total directly against the source would
-- require dividing the source by 1.2, and that comparison can never be
-- exact: net_revenue is rounded to pence PER LINE, and because almost
-- every price ends in .99, those roundings are systematically upward
-- (31,514 lines round up vs 9,542 down, a net +£85.94 on £1.21m, 0.007%).
-- Per-line rounding is the correct behaviour -- it is what an invoice and
-- a tax authority actually see -- so the fix is to compare quantities that
-- SHOULD be identical, not to widen a tolerance until a lossy comparison
-- passes. A tolerance wide enough to absorb £86 would also absorb a real
-- fan-out bug.
--
-- Hop 1: source -> fct_order_line, on the untransformed VAT-inclusive
--        value. Exact, because no arithmetic has been applied yet.
-- Hop 2: fct_order_line -> mart_daily_trading, on ex-VAT net_revenue.
--        Exact, because the mart only sums what the fact already computed.

with hop1_fact as (
    select sum(net_revenue_incl_vat) as total
    from {{ ref('fct_order_line') }}
    where not is_cancelled
),

hop1_source as (
    -- The as_of filter MUST be mirrored here. fct_order_line is filtered to
    -- rows available as of the cursor; comparing it against an unfiltered
    -- source makes this test fail at every historical cursor, which would
    -- break the backtest that rebuilds the warehouse across many as_of dates.
    select sum(l.price * l.quantity - l.total_discount) as total
    from {{ source('raw', 'order_lines') }} l
    inner join {{ source('raw', 'orders') }} o
        on o.id = l.order_id
    where o.cancelled_at is null
      and {{ as_of_filter('o._weld_synced', 'o.created_at') }}
),

hop2_fact as (
    select sum(net_revenue) as total
    from {{ ref('fct_order_line') }}
    where not is_cancelled
),

hop2_mart as (
    select sum(net_revenue) as total
    from {{ ref('mart_daily_trading') }}
)

select 'hop1_source_to_fact' as hop, f.total as a, s.total as b, f.total - s.total as difference
from hop1_fact f cross join hop1_source s
where abs(f.total - s.total) > 0.01

union all

select 'hop2_fact_to_mart', m.total, f.total, m.total - f.total
from hop2_mart m cross join hop2_fact f
where abs(m.total - f.total) > 0.01
