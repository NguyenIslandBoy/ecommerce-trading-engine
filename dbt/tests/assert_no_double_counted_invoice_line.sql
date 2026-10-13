{#
  An invoice line is counted once per channel, and every visible line
  appears. A duplicated allocation row would otherwise double a channel's
  invoiced amount; a dropped line would understate it.
#}

with dupes as (
    select invoice_line_id, channel, 'duplicate' as issue
    from {{ ref('fct_invoice_allocated') }}
    group by invoice_line_id, channel
    having count(*) > 1
),

missing as (
    select l.invoice_line_id, cast(null as varchar) as channel, 'missing' as issue
    from {{ ref('stg_vendor_invoice_lines') }} l
    where not exists (
        select 1 from {{ ref('fct_invoice_allocated') }} f
        where f.invoice_line_id = l.invoice_line_id
    )
)

select * from dupes
union all
select * from missing
