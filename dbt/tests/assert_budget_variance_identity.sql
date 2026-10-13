{#
  On every row, with the variance convention stated once and checked
  everywhere:
    budget_variance     = recognised_media_expense - approved_budget
                          (positive = overspend)
    remaining_budget    = approved_budget - recognised_media_expense
    budget_variance_pct = budget_variance / approved_budget, NULL when the
                          budget is NULL or 0
  NULL on either side gives NULL, never 0.
#}

select month_start, channel, approved_budget, recognised_media_expense,
       budget_variance, remaining_budget, budget_variance_pct
from {{ ref('mart_marketing_budget_vs_actual') }}
where budget_variance  is distinct from recognised_media_expense - approved_budget
   or remaining_budget is distinct from approved_budget - recognised_media_expense
   or budget_variance_pct is distinct from cast(
          (recognised_media_expense - approved_budget) / nullif(approved_budget, 0)
          as decimal(12,6))
