{#
  Over a full month the daily plan sums to the approved budget to the
  penny, for every month x channel that has a budget. An unrecognised
  pacing profile plans NULL and fails here.
#}

select
    month_start,
    channel,
    any_value(approved_budget)  as approved_budget,
    sum(planned_spend)          as planned_total,
    count(*)                    as days,
    any_value(days_in_month)    as days_in_month
from {{ ref('mart_marketing_pacing_daily') }}
where approved_budget is not null
group by month_start, channel
having sum(planned_spend) is distinct from any_value(approved_budget)
    or count(planned_spend) != any_value(days_in_month)
