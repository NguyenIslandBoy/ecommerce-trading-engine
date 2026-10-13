{#
  SYNTHETIC. One row per invoice line x channel. Allocations carry no date of
  their own: they become visible with their invoice line. That is enforced in
  fct_invoice_allocated, which starts from the visible lines, because staging
  may not join across sources.
#}

select
    invoice_line_id,
    channel,
    cast(allocation_pct as decimal(6,4))            as allocation_pct,
    cast(is_synthetic as boolean)                   as is_synthetic

from {{ source('finance', 'invoice_line_allocations') }}
