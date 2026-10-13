{#
  SYNTHETIC Finance inputs. Month x channel budget against recognised
  expense. Every cost figure is read from fct_marketing_cost_monthly and
  never recomputed here, so this mart and mart_marketing_invoice_recon
  cannot disagree.

  approved_budget is the LATEST version visible at the cursor (by
  approved_on): a revision replaces the original from the day it is
  approved. budget_version_used says which.

  The synthetic budgets are MEDIA budgets: agency fees are not budgeted in
  this data. So the comparison is against recognised_media_expense
  (GREATEST(delivered, media_invoiced)); fee_invoiced and the all-in
  recognised_expense are shown alongside, not compared.

  Variance convention: budget_variance = recognised_media_expense -
  approved_budget. Positive means overspend. NULL whenever either side is
  NULL: TikTok has a budget but no cost source, and must never read as a
  100% underspend.

  A partial_month row compares a part-month of expense with a full-month
  budget; read its variance with data_status in view.
#}

with budget as (

    select
        budget_month                                        as month_start,
        channel,
        approved_amount,
        budget_version,
        pacing_profile,
        row_number() over (
            partition by budget_month, channel
            order by approved_on desc,
                     case budget_version when 'revised' then 0 else 1 end
        )                                                   as recency
    from {{ ref('stg_marketing_budget') }}

)

select
    c.month_start,
    c.channel,
    b.approved_amount                                       as approved_budget,
    b.budget_version                                        as budget_version_used,
    b.pacing_profile,
    c.delivered_spend,
    c.media_invoiced,
    c.fee_invoiced,
    c.invoiced_amount,
    c.estimated_unbilled_accrual,
    c.recognised_media_expense,
    c.recognised_expense,
    cast(b.approved_amount - c.recognised_media_expense as decimal(12,2))
                                                            as remaining_budget,
    cast(c.recognised_media_expense - b.approved_amount as decimal(12,2))
                                                            as budget_variance,
    cast((c.recognised_media_expense - b.approved_amount)
         / nullif(b.approved_amount, 0) as decimal(12,6))   as budget_variance_pct,
    c.data_status,
    true                                                    as is_synthetic_finance_input,
    -- The cursor this build ran at, so an export can say what it knew when.
    cast('{{ var("as_of_date") }}' as date)                as as_of_date

from {{ ref('fct_marketing_cost_monthly') }} c
left join budget b
    on b.month_start = c.month_start
   and b.channel = c.channel
   and b.recency = 1
