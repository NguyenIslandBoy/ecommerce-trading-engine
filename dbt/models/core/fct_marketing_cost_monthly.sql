{#
  SYNTHETIC Finance inputs reconciled against real-shaped delivered spend.
  Grain: month x channel, channels meta / google / tiktok on every month of
  the date spine, plus an 'unallocated' row in any month with unallocated
  invoice lines. This is the single source of truth for the cost figures;
  the marts read them from here and never recompute them.

  delivered_spend   Sum of fct_ad_spend_daily for the month, visible days
                    only (staging already applies the cursor and the 1-day
                    ingestion lag). NULL where the channel has no cost source
                    (tiktok, unallocated) and where no day is visible yet.
  media_invoiced    Allocated platform media lines whose service month is
                    this month and whose received_date is visible.
  fee_invoiced      The same for agency fee lines.
  invoiced_amount   media_invoiced + fee_invoiced. 0.00 means "no invoice
                    received yet", a known zero, not missing data.

  is_media_final    A platform media invoice covering the WHOLE service
                    month (service period = the calendar month) has been
                    received. The final invoice is then the authority on what
                    the month cost.

  Accrual rule (only delivered-but-unbilled MEDIA cost is accrued), first
  match wins:
    final invoice received (is_media_final):
      estimated_unbilled_accrual = 0
      recognised_media_expense   = media_invoiced
      -- the TRUE-UP: the month-end estimate is replaced by the invoice, in
      -- both directions; the size of the true-up is the recon difference
    delivered known, no final invoice yet:
      estimated_unbilled_accrual = MAX(delivered_spend - media_invoiced, 0)
      recognised_media_expense   = GREATEST(delivered_spend, media_invoiced)
    delivered NULL:
      estimated_unbilled_accrual = NULL
      recognised_media_expense   = media_invoiced if any media line exists,
                                   else NULL
  recognised_expense = recognised_media_expense + fee_invoiced (or, with no
  media figure, invoiced_amount if any invoice line exists, else NULL).

  The synthetic budgets are MEDIA budgets, so budget variance is measured
  against recognised_media_expense, not recognised_expense.

  Fees are compared with nothing: delivered spend is media only, so adding
  fees to the comparison would turn every month into a variance. A fee is
  recognised when its invoice is received; with no contract data it cannot
  be accrued. There is no GL here: the true-up restates the month's
  figures, it does not post journal entries.

  data_status, first match wins:
    no_cost_source  the channel has no cost file (tiktok)
    unallocated     the bucket for invoice lines with no allocation
    partial_month   the month is not complete at the cursor: its last day's
                    spend is not yet visible (month_end >= as_of_date)
    source_gap      complete month, but fewer days of spend than days in the
                    month (meta 2025-03-15/16 never arrived)
    complete        otherwise
#}

with months as (

    select distinct month_start
    from {{ ref('dim_date') }}

),

grid as (

    select m.month_start, c.channel, c.has_cost_source
    from months m
    cross join (
        select * from (values ('meta', true), ('google', true), ('tiktok', false))
            as t(channel, has_cost_source)
    ) c

),

delivered as (

    select
        cast(date_trunc('month', ad_date) as date)          as month_start,
        platform                                            as channel,
        sum(spend)                                          as delivered_spend,
        count(distinct ad_date)                             as delivered_days
    from {{ ref('fct_ad_spend_daily') }}
    group by all

),

invoiced as (

    select
        service_month                                       as month_start,
        channel,
        sum(allocated_amount) filter (where cost_type = 'media')
                                                            as media_invoiced,
        sum(allocated_amount) filter (where cost_type = 'fee')
                                                            as fee_invoiced,
        count(*)                                            as invoice_rows,
        count(*) filter (where cost_type = 'media')         as media_rows,
        count(*) filter (where cost_type = 'media'
                           and service_period_start = service_month
                           and service_period_end = last_day(service_month))
                                                            as media_final_rows
    from {{ ref('fct_invoice_allocated') }}
    group by all

),

unallocated_months as (

    select month_start, 'unallocated' as channel, false as has_cost_source
    from invoiced
    where channel = 'unallocated'

),

base as (

    select
        g.month_start,
        g.channel,
        g.has_cost_source,
        last_day(g.month_start)                             as month_end,
        extract(day from last_day(g.month_start))           as days_in_month,
        case when g.has_cost_source then d.delivered_spend end
                                                            as delivered_spend,
        coalesce(d.delivered_days, 0)                       as delivered_days,
        cast(coalesce(i.media_invoiced, 0) as decimal(12,2)) as media_invoiced,
        cast(coalesce(i.fee_invoiced, 0) as decimal(12,2))  as fee_invoiced,
        coalesce(i.invoice_rows, 0)                         as invoice_rows,
        coalesce(i.media_rows, 0)                           as media_rows,
        g.has_cost_source and coalesce(i.media_final_rows, 0) > 0
                                                            as is_media_final
    from (select * from grid union all select * from unallocated_months) g
    left join delivered d
        on d.month_start = g.month_start and d.channel = g.channel
    left join invoiced i
        on i.month_start = g.month_start and i.channel = g.channel

)

select
    month_start,
    channel,
    has_cost_source,
    cast(delivered_spend as decimal(12,2))                  as delivered_spend,
    media_invoiced,
    fee_invoiced,
    cast(media_invoiced + fee_invoiced as decimal(12,2))    as invoiced_amount,
    is_media_final,
    cast(case
        when delivered_spend is null                        then null
        when is_media_final                                 then 0
        else greatest(delivered_spend - media_invoiced, 0)
    end as decimal(12,2))                                   as estimated_unbilled_accrual,
    cast(case
        when is_media_final
            then media_invoiced + fee_invoiced
        when delivered_spend is not null
            then greatest(delivered_spend, media_invoiced) + fee_invoiced
        when invoice_rows > 0
            then media_invoiced + fee_invoiced
    end as decimal(12,2))                                   as recognised_expense,
    cast(case
        when is_media_final
            then media_invoiced
        when delivered_spend is not null
            then greatest(delivered_spend, media_invoiced)
        when media_rows > 0
            then media_invoiced
    end as decimal(12,2))                                   as recognised_media_expense,
    delivered_days,
    days_in_month,
    invoice_rows,
    case
        when channel = 'unallocated'                        then 'unallocated'
        when not has_cost_source                            then 'no_cost_source'
        when month_end >= cast('{{ var("as_of_date") }}' as date)
                                                            then 'partial_month'
        when delivered_days < days_in_month                 then 'source_gap'
        else 'complete'
    end                                                     as data_status
from base
