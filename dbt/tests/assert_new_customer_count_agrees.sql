-- dim_customer and fct_order derive "first order" independently; a
-- divergence would silently shift every CAC denominator computed from
-- mart_daily_trading.new_customers. All three are currently 20,284.

with dim_count as (
    select count(*) as n
    from {{ ref('dim_customer') }}
    where first_order_date is not null
),

fct_count as (
    select count(*) as n
    from {{ ref('fct_order') }}
    where is_first_order
),

mart_count as (
    select sum(new_customers) as n
    from {{ ref('mart_daily_trading') }}
)

select d.n as dim_customer_count, f.n as fct_order_count, m.n as mart_new_customers
from dim_count d
cross join fct_count f
cross join mart_count m
where not (d.n = f.n and f.n = m.n)
