{#
  TikTok has no cost file. Its delivered spend, accrual and (with no
  invoices) recognised expense must be NULL in every month: a 0 would read
  as free acquisition, a 100% underspend against budget, or a CAC
  improvement. The second branch stops this passing vacuously if the TikTok
  rows disappear from the fact.
#}

select month_start, channel, 'zero_not_null' as issue
from {{ ref('fct_marketing_cost_monthly') }}
where channel in ('tiktok', 'unallocated')
  and (delivered_spend is not null
       or estimated_unbilled_accrual is not null
       or (invoice_rows = 0 and recognised_expense is not null))

union all

select d.month_start, 'tiktok', 'missing_row'
from (select distinct month_start from {{ ref('dim_date') }}) d
where not exists (
    select 1 from {{ ref('fct_marketing_cost_monthly') }} m
    where m.channel = 'tiktok' and m.month_start = d.month_start
)
