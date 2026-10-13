{#
  SYNTHETIC inputs. Grain: invoice line x channel.

  Each allocated row is ROUND(amount * allocation_pct, 2). The LAST row of a
  line (by channel) takes the remainder, so a line whose allocations sum to
  100% is allocated to the penny. The remainder is taken against
  ROUND(amount * total_pct, 2), not against the line amount: if a line's
  allocations sum to 90%, the shortfall stays visible rather than being
  silently absorbed by the last channel, and
  assert_invoice_allocation_sums_to_line fails on it.

  A line with NO allocation rows becomes one row with channel 'unallocated'
  and allocation_status 'unallocated'. It is never dropped: dropping it would
  understate spend with no trace.

  Allocations are visible only with their line: this model starts from
  stg_vendor_invoice_lines, which is already filtered to received_date <=
  as_of_date.
#}

with lines as (

    select * from {{ ref('stg_vendor_invoice_lines') }}

),

allocated as (

    select
        l.invoice_line_id,
        a.channel,
        a.allocation_pct,
        cast(round(l.amount * a.allocation_pct, 2) as decimal(12,2))      as rounded_amount,
        row_number() over (partition by l.invoice_line_id order by a.channel desc) = 1
                                                                           as is_last_row,
        sum(a.allocation_pct) over (partition by l.invoice_line_id)        as total_pct,
        sum(round(l.amount * a.allocation_pct, 2))
            over (partition by l.invoice_line_id)                          as sum_rounded
    from lines l
    inner join {{ ref('stg_invoice_line_allocations') }} a
        on a.invoice_line_id = l.invoice_line_id

),

with_amount as (

    select
        al.invoice_line_id,
        al.channel,
        al.allocation_pct,
        case
            when al.is_last_row then cast(
                round(l.amount * al.total_pct, 2)
                - (al.sum_rounded - al.rounded_amount) as decimal(12,2))
            else al.rounded_amount
        end                                                                as allocated_amount,
        'allocated'                                                        as allocation_status
    from allocated al
    inner join lines l on l.invoice_line_id = al.invoice_line_id

    union all

    select
        l.invoice_line_id,
        'unallocated'                                                      as channel,
        cast(null as decimal(6,4))                                         as allocation_pct,
        l.amount                                                           as allocated_amount,
        'unallocated'                                                      as allocation_status
    from lines l
    where not exists (
        select 1 from {{ ref('stg_invoice_line_allocations') }} a
        where a.invoice_line_id = l.invoice_line_id
    )

)

select
    w.invoice_line_id,
    l.invoice_id,
    l.vendor,
    l.billed_channel,
    l.cost_type,
    l.service_month,
    l.service_period_start,
    l.service_period_end,
    l.invoice_date,
    l.received_date,
    l.line_description,
    w.channel,
    w.allocation_pct,
    l.amount                                                               as line_amount,
    w.allocated_amount,
    w.allocation_status,
    l.is_synthetic
from with_amount w
inner join lines l on l.invoice_line_id = w.invoice_line_id
