{#
  Every visible invoice line is allocated to the penny: its rows in
  fct_invoice_allocated sum to the line amount in staging. Compared against
  staging, not against fct's own line_amount column, so a line that vanishes
  from the fact also fails. Allocations that do not sum to 100% fail here,
  because the remainder row absorbs rounding only, not a misallocation.
#}

select
    l.invoice_line_id,
    l.amount                        as line_amount,
    sum(f.allocated_amount)         as allocated_total
from {{ ref('stg_vendor_invoice_lines') }} l
left join {{ ref('fct_invoice_allocated') }} f
    on f.invoice_line_id = l.invoice_line_id
group by l.invoice_line_id, l.amount
having sum(f.allocated_amount) is distinct from l.amount
