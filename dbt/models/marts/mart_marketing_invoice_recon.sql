{#
  SYNTHETIC Finance inputs. Month x channel: delivered platform spend
  against platform media invoices. Figures are read from
  fct_marketing_cost_monthly, never recomputed.

  The comparison is MEDIA ONLY. Agency fees (fee_invoiced) are shown but not
  compared: delivered spend contains no fees, so including them would turn
  every month into an exception.

    difference      media_invoiced - delivered_spend (positive: billed more
                    than the platform extract shows)
    difference_pct  difference / delivered_spend

  recon_status, first match wins:
    unallocated_cost   the bucket for invoice lines with no allocation
    no_cost_source     the channel has no cost file (tiktok)
    unbilled           no media invoice received yet for the month
                       (platforms bill in arrears)
    within_tolerance   |difference_pct| <= var('recon_tolerance_pct')
    exception          otherwise
#}

select
    month_start,
    channel,
    delivered_spend,
    media_invoiced,
    fee_invoiced,
    invoiced_amount,
    estimated_unbilled_accrual,
    cast(case when delivered_spend is not null and media_invoiced > 0
              then media_invoiced - delivered_spend end
         as decimal(12,2))                                  as difference,
    cast(case when delivered_spend is not null and media_invoiced > 0
              then (media_invoiced - delivered_spend) / nullif(delivered_spend, 0) end
         as decimal(12,6))                                  as difference_pct,
    cast(case when channel = 'unallocated' then invoiced_amount else 0 end
         as decimal(12,2))                                  as unallocated_invoiced_amount,
    case
        when channel = 'unallocated'                        then 'unallocated_cost'
        when not has_cost_source                            then 'no_cost_source'
        when media_invoiced = 0                             then 'unbilled'
        when abs(media_invoiced - delivered_spend)
             <= abs(delivered_spend) * {{ var('recon_tolerance_pct') }} / 100.0
                                                            then 'within_tolerance'
        else 'exception'
    end                                                     as recon_status,
    data_status,
    true                                                    as is_synthetic_finance_input

from {{ ref('fct_marketing_cost_monthly') }}
