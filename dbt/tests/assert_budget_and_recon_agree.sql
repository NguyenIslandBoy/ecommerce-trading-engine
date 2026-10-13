{#
  The budget and recon marts show identical delivered, invoiced and accrual
  figures for every month x channel, and the same set of rows. Both read
  fct_marketing_cost_monthly; this fails if either starts recomputing.
#}

select
    coalesce(b.month_start, r.month_start)  as month_start,
    coalesce(b.channel, r.channel)          as channel
from {{ ref('mart_marketing_budget_vs_actual') }} b
full outer join {{ ref('mart_marketing_invoice_recon') }} r
    on r.month_start = b.month_start and r.channel = b.channel
where b.month_start is null
   or r.month_start is null
   or b.delivered_spend            is distinct from r.delivered_spend
   or b.media_invoiced             is distinct from r.media_invoiced
   or b.fee_invoiced               is distinct from r.fee_invoiced
   or b.invoiced_amount            is distinct from r.invoiced_amount
   or b.estimated_unbilled_accrual is distinct from r.estimated_unbilled_accrual
