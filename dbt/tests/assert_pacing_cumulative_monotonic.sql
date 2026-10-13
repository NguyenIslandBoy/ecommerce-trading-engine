{#
  Within a month, the cumulative plan never decreases: no day plans
  negative spend. A negative budget or a broken weight curve fails here.
#}

select date_day, channel, planned_cumulative, previous_cumulative
from (
    select
        date_day,
        channel,
        planned_cumulative,
        lag(planned_cumulative) over (
            partition by channel, month_start order by date_day
        ) as previous_cumulative
    from {{ ref('mart_marketing_pacing_daily') }}
)
where planned_cumulative < previous_cumulative
