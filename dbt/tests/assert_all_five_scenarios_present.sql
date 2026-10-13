{{ config(severity='warn') }}
{#
  Each scenario seeded by scripts/generate_finance_inputs.py produces its
  expected outcome at the DEFAULT cursor. Warn, not error: at an earlier
  cursor some scenarios have legitimately not happened yet (June is not
  unbilled in January). One row per scenario that did not land.
#}

with checks as (

    -- 1. Forecast overspend: Meta 2025-05 forecasts an overspend mid-month
    --    (the row for the 14th uses spend through the 14th only) and ends over.
    select '1_forecast_overspend' as scenario,
           exists (select 1 from {{ ref('mart_marketing_pacing_daily') }}
                   where channel = 'meta' and date_day = date '2025-05-14'
                     and forecast_overspend > 0)
       and exists (select 1 from {{ ref('mart_marketing_budget_vs_actual') }}
                   where channel = 'meta' and month_start = date '2025-05-01'
                     and budget_variance > 0)       as ok

    union all
    -- 2. Delivered in M, invoiced in M+1: a delivered month with no media
    --    invoice yet, carried as an accrual.
    select '2_unbilled_accrual',
           exists (select 1 from {{ ref('mart_marketing_invoice_recon') }}
                   where recon_status = 'unbilled' and estimated_unbilled_accrual > 0)

    union all
    -- 3. Agency invoice line split 60/40 across meta and google.
    select '3_agency_split',
           exists (select 1 from {{ ref('fct_invoice_allocated') }} m
                   join {{ ref('fct_invoice_allocated') }} g using (invoice_line_id)
                   where m.billed_channel = 'agency'
                     and m.channel = 'meta'   and m.allocation_pct = 0.6
                     and g.channel = 'google' and g.allocation_pct = 0.4)

    union all
    -- 4. Missing cost source: TikTok has a budget, and NULL spend and
    --    variance, in every month.
    select '4_tiktok_null_not_zero',
           exists (select 1 from {{ ref('mart_marketing_budget_vs_actual') }} where channel = 'tiktok')
       and not exists (select 1 from {{ ref('mart_marketing_budget_vs_actual') }}
                       where channel = 'tiktok'
                         and (approved_budget is null
                              or delivered_spend is not null
                              or budget_variance is not null))

    union all
    -- 5. Unallocated invoice line surfaces as its own cost row.
    select '5_unallocated_line',
           exists (select 1 from {{ ref('mart_marketing_invoice_recon') }}
                   where recon_status = 'unallocated_cost' and invoiced_amount > 0)

    union all
    -- Recon exception above var('recon_tolerance_pct').
    select 'x_recon_exception',
           exists (select 1 from {{ ref('mart_marketing_invoice_recon') }}
                   where recon_status = 'exception')

)

select scenario from checks where not ok
