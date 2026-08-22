{#
  Point-in-time availability filter.

  A row is visible only once it has been ingested. `_weld_synced` is the
  ingestion timestamp and is the correct basis; where it is absent
  (customers.csv is blank throughout, and DuckDB infers it as VARCHAR)
  we fall back to the row's own event timestamp.

  try_cast is required: a blank string is not castable to timestamp.
#}
{% macro as_of_filter(sync_col, event_col) %}
    cast(
        coalesce(
            try_cast({{ sync_col }} as timestamp),
            cast({{ event_col }} as timestamp)
        ) as date
    ) <= cast('{{ var("as_of_date") }}' as date)
{% endmacro %}
