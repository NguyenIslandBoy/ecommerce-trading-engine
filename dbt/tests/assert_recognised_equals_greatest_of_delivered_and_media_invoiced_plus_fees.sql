{#
  The accrual rule, recomputed independently from the upstream facts rather
  than from the model's own columns:

    delivered known: recognised = GREATEST(delivered, media_invoiced) + fees
                     accrual    = MAX(delivered - media_invoiced, 0)
    delivered NULL:  recognised = invoiced if any invoice exists, else NULL
                     accrual    = NULL

  Media is a platform-billed line, fees an agency-billed one, classified here
  from billed_channel directly rather than the model's cost_type.
#}

with delivered as (
    select cast(date_trunc('month', ad_date) as date) as month_start,
           platform as channel,
           sum(spend) as delivered
    from {{ ref('fct_ad_spend_daily') }}
    group by all
),

inv as (
    select service_month as month_start,
           channel,
           sum(allocated_amount) filter (where billed_channel != 'agency') as media,
           sum(allocated_amount) filter (where billed_channel = 'agency')  as fees,
           count(*) as n
    from {{ ref('fct_invoice_allocated') }}
    group by all
),

expected as (
    select
        m.month_start,
        m.channel,
        case
            when m.channel in ('meta', 'google') and d.delivered is not null
                then greatest(d.delivered, coalesce(i.media, 0)) + coalesce(i.fees, 0)
            when coalesce(i.n, 0) > 0
                then coalesce(i.media, 0) + coalesce(i.fees, 0)
        end as expected_recognised,
        case
            when m.channel in ('meta', 'google') and d.delivered is not null
                then greatest(d.delivered - coalesce(i.media, 0), 0)
        end as expected_accrual,
        m.recognised_expense,
        m.estimated_unbilled_accrual
    from {{ ref('fct_marketing_cost_monthly') }} m
    left join delivered d on d.month_start = m.month_start and d.channel = m.channel
    left join inv i       on i.month_start = m.month_start and i.channel = m.channel
)

select *
from expected
where recognised_expense is distinct from expected_recognised
   or estimated_unbilled_accrual is distinct from expected_accrual
