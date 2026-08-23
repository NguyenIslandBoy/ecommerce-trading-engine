-- dim_customer and fct_order derive "first order" independently; a
-- divergence would silently shift every CAC denominator computed from
-- mart_daily_trading.new_customers. All three totals are currently 20,284.
--
-- Totals alone are not enough: a definitional drift that reassigns which
-- CHANNEL a customer is attributed to (while every customer still counts
-- as "first order" exactly once, somewhere) would keep all three totals
-- equal while silently corrupting every per-channel CAC. So this also
-- compares mart_daily_trading.new_customers against mart_ltv.cohort_size
-- per (month x channel) cell -- the same two independently-derived "first
-- order" paths, at the grain the ruling actually needs to hold at.
-- Verified: all 48 cells (12 months x 4 channels) agree exactly today.

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
),

totals_check as (

    select
        'total' as check,
        cast(d.n as varchar) as a_label,
        cast(f.n as varchar) as b_label,
        d.n as a_value,
        f.n as b_value
    from dim_count d
    cross join fct_count f
    cross join mart_count m
    where not (d.n = f.n and f.n = m.n)

),

daily_trading_by_cell as (
    select
        cast(date_trunc('month', date_day) as date) as month_start,
        channel,
        sum(new_customers)                          as new_customers
    from {{ ref('mart_daily_trading') }}
    group by 1, 2
),

ltv_by_cell as (
    -- cohort_size is repeated across all three horizon_days rows for a
    -- given cohort_month x acquisition_channel; pick one horizon so each
    -- cell is counted once.
    select
        cohort_month,
        acquisition_channel,
        cohort_size
    from {{ ref('mart_ltv') }}
    where horizon_days = 30
),

cell_check as (

    select
        'month_x_channel' as check,
        coalesce(cast(d.month_start as varchar), cast(l.cohort_month as varchar)) || ' / ' ||
            coalesce(d.channel, l.acquisition_channel)  as a_label,
        cast(null as varchar)                           as b_label,
        d.new_customers                                 as a_value,
        l.cohort_size                                   as b_value
    from daily_trading_by_cell d
    full outer join ltv_by_cell l
        on l.cohort_month = d.month_start
       and l.acquisition_channel = d.channel
    where coalesce(d.new_customers, -1) != coalesce(l.cohort_size, -1)

)

select * from totals_check
union all
select * from cell_check
