{#
  SYNTHETIC. One row per budget version. A budget is knowable from the day it
  was approved, so at cursor D only versions with approved_on <= D exist. The
  file has no ingestion timestamp, so as_of_filter does not apply; the
  approval date is the availability date and is filtered explicitly.
#}

select
    budget_id,
    cast(budget_month as date)                      as budget_month,
    channel,
    budget_version,
    cast(approved_amount as decimal(12,2))          as approved_amount,
    currency,
    pacing_profile,
    cast(approved_on as date)                       as approved_on,
    cast(is_synthetic as boolean)                   as is_synthetic

from {{ source('finance', 'marketing_budget') }}
where cast(approved_on as date) <= cast('{{ var("as_of_date") }}' as date)
