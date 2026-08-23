with src as (
    select * from {{ source('raw', 'customers') }}
)

select
    id                                              as customer_id,
    nullif(lower(trim(email)), '')                  as customer_email,
    nullif(trim(email), '') is not null             as has_valid_email,
    -- Source-provided aggregate. Counts CANCELLED orders, so it will not
    -- match a derived count; the discrepancy is documented in the README,
    -- not detected in mart_data_quality (which only tracks missing_day
    -- rows for three sources).
    cast(order_count as bigint)                     as source_order_count,
    cast(total_spent as decimal(12,2))              as source_total_spent,
    state,
    accepts_marketing,
    email_marketing_consent_state                   as email_consent_state,
    cast(created_at as timestamp)                   as created_at,
    cast(created_at as date)                        as signup_date

from src
where {{ as_of_filter('_weld_synced', 'created_at') }}
