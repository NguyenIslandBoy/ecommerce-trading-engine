-- Guardrail. orders.email and customers.email differ for 86% of orders
-- (same local part, different domain). If anyone ever joins on email,
-- this test shows how catastrophic it would be: the join loses the
-- overwhelming majority of orders.
--
-- Fails if an email-based join would match MORE than 20% of orders,
-- which would mean the data changed and this guardrail needs revisiting.

with email_join as (
    select count(*) as matched
    from {{ ref('stg_orders') }} o
    inner join {{ ref('stg_customers') }} c
        on c.customer_email = o.order_email
),

id_join as (
    select count(*) as matched
    from {{ ref('stg_orders') }} o
    inner join {{ ref('stg_customers') }} c
        on c.customer_id = o.customer_id
)

select
    e.matched as email_matched,
    i.matched as id_matched
from email_join e
cross join id_join i
where e.matched > i.matched * 0.20
