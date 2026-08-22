{#
  Customers are keyed on customer_id ONLY.

  orders.email differs from customers.email for 86% of orders (same local
  part, different domain). Any join or dedup on email corrupts every
  customer-level metric in the warehouse.

  Acquisition channel and first order date derive from non-cancelled
  orders only, so a customer whose only order was cancelled has a NULL
  first_order_date and does not enter any cohort.
#}

with first_order as (

    select
        customer_id,
        min(order_date)                                 as first_order_date,
        arg_min(channel, created_at)                    as acquisition_channel
    from {{ ref('stg_orders') }}
    where not is_cancelled
    group by customer_id

)

select
    c.customer_id,
    c.customer_email,
    c.has_valid_email,
    c.has_valid_email and c.email_consent_state = 'subscribed'  as is_marketable,
    c.email_consent_state,
    c.accepts_marketing,
    c.signup_date,
    f.first_order_date,
    cast(date_trunc('month', f.first_order_date) as date)       as first_order_month,
    coalesce(f.acquisition_channel, 'unattributed')             as acquisition_channel,
    c.source_order_count,
    c.source_total_spent

from {{ ref('stg_customers') }} c
left join first_order f
    on f.customer_id = c.customer_id
