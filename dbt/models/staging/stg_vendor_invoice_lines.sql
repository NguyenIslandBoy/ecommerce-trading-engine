{#
  SYNTHETIC. One row per invoice line. A line is knowable from the day it was
  received (not issued), so at cursor D only lines with received_date <= D
  exist. That gap is what makes an accrual necessary at month-end close.

  cost_type separates platform media (billed by meta or google, comparable to
  delivered spend) from agency fees (billed by the agency, NOT in delivered
  spend). Derived from the single column billed_channel.
#}

select
    invoice_line_id,
    invoice_id,
    vendor,
    billed_channel,
    case when billed_channel = 'agency' then 'fee' else 'media' end
                                                    as cost_type,
    cast(service_period_start as date)              as service_period_start,
    cast(service_period_end as date)                as service_period_end,
    cast(date_trunc('month', cast(service_period_start as date)) as date)
                                                    as service_month,
    cast(invoice_date as date)                      as invoice_date,
    cast(received_date as date)                     as received_date,
    line_description,
    cast(amount as decimal(12,2))                   as amount,
    currency,
    cast(is_synthetic as boolean)                   as is_synthetic

from {{ source('finance', 'vendor_invoice_lines') }}
where cast(received_date as date) <= cast('{{ var("as_of_date") }}' as date)
