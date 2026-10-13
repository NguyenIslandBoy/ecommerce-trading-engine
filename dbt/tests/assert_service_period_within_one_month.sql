{#
  Every service period sits inside one calendar month and runs forwards.
  The monthly cost fact assigns a line to the month of service_period_start;
  a period spanning two months would put all of it in the first.
#}

select
    invoice_line_id,
    service_period_start,
    service_period_end
from {{ ref('stg_vendor_invoice_lines') }}
where date_trunc('month', service_period_start) != date_trunc('month', service_period_end)
   or service_period_end < service_period_start
