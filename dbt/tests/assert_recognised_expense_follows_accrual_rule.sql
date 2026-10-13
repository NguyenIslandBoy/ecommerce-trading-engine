{#
  The accrual rule, recomputed independently from the upstream facts rather
  than from the model's own columns. First match wins:

    final invoice received (a platform media line covering the whole month):
                     recognised_media = media_invoiced          -- the true-up
                     accrual          = 0
    delivered known: recognised_media = GREATEST(delivered, media_invoiced)
                     accrual          = MAX(delivered - media_invoiced, 0)
    delivered NULL:  recognised_media = media_invoiced if any media line, else NULL
                     accrual          = NULL

    recognised = recognised_media + fees, or invoiced if any invoice exists
                 when there is no media figure, else NULL

  Media is a platform-billed line, fees an agency-billed one, classified here
  from billed_channel directly rather than the model's cost_type; "final" is
  derived from the service period here rather than read from is_media_final.
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
           count(*) as n,
           count(*) filter (where billed_channel != 'agency') as n_media,
           count(*) filter (where billed_channel != 'agency'
                              and service_period_start = service_month
                              and service_period_end = last_day(service_month)) as n_final
    from {{ ref('fct_invoice_allocated') }}
    group by all
),

expected as (
    select
        m.month_start,
        m.channel,
        m.channel in ('meta', 'google') and coalesce(i.n_final, 0) > 0 as final,
        d.delivered,
        i.media,
        i.fees,
        i.n,
        i.n_media,
        m.is_media_final,
        m.recognised_media_expense,
        m.recognised_expense,
        m.estimated_unbilled_accrual
    from {{ ref('fct_marketing_cost_monthly') }} m
    left join delivered d on d.month_start = m.month_start and d.channel = m.channel
    left join inv i       on i.month_start = m.month_start and i.channel = m.channel
),

rule as (
    select
        *,
        case
            when final then media
            when channel in ('meta', 'google') and delivered is not null
                then greatest(delivered, coalesce(media, 0))
            when coalesce(n_media, 0) > 0 then media
        end as expected_media,
        case
            when not (channel in ('meta', 'google') and delivered is not null) then null
            when final then 0
            else greatest(delivered - coalesce(media, 0), 0)
        end as expected_accrual
    from expected
)

select *
from rule
where is_media_final is distinct from final
   or recognised_media_expense is distinct from expected_media
   or estimated_unbilled_accrual is distinct from expected_accrual
   or recognised_expense is distinct from (
        case
            when expected_media is not null then expected_media + coalesce(fees, 0)
            when coalesce(n, 0) > 0 then coalesce(media, 0) + coalesce(fees, 0)
        end)
