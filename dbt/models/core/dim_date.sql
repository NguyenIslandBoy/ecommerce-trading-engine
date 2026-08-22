{#
  A generous fixed spine, trimmed to the observed data window and the
  as_of cursor. Generating from a literal range keeps the model
  deterministic; the join to bounds is what makes it honest.
#}

with bounds as (
    select
        min(order_date)                                     as first_day,
        -- Clamp to the last day that actually has data. The as_of cursor sits
        -- one day past the period close (see dbt_project.yml), so using it
        -- directly would append a trailing empty day to the spine and to every
        -- mart built on it.
        least(cast('{{ var("as_of_date") }}' as date),
              max(order_date))                              as last_day
    from {{ ref('stg_orders') }}
),

spine as (
    select cast(unnest as date) as date_day
    from unnest(generate_series(date '2024-01-01', date '2026-12-31', interval 1 day))
)

select
    s.date_day,
    extract(year from s.date_day)                       as year,
    extract(month from s.date_day)                      as month,
    strftime(s.date_day, '%Y-%m')                       as year_month,
    extract(isodow from s.date_day)                     as iso_dow,
    extract(week from s.date_day)                       as iso_week,
    cast(date_trunc('week', s.date_day) as date)        as week_start,
    cast(date_trunc('month', s.date_day) as date)       as month_start,
    extract(isodow from s.date_day) in (6, 7)           as is_weekend,
    extract(month from s.date_day) in (11, 12)          as is_peak_season

from spine s
cross join bounds b
where s.date_day between b.first_day and b.last_day
