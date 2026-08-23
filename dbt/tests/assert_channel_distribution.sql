{#
  Pins each channel's share of non-cancelled orders. This guards the
  MAPPING inside channel_from_referrer.sql, not just the value domain --
  accepted_values on `channel` only checks that a value is one of the
  four allowed strings. Swapping two arms (e.g. youtube -> meta) keeps
  every value in-domain, inverts every channel CAC, ROAS and LTV in the
  warehouse, and leaves all other tests, including accepted_values, green.

  Verified shares: google 36.5%, meta 27.5%, unattributed 26.9%,
  tiktok 9.0%. +/-2 percentage-point tolerance: wide enough to survive a
  legitimate data refresh, far too tight to survive a swapped CASE arm
  (meta and google alone are 9pp apart, so a swap between them would miss
  by roughly 9pp).
#}

with actual as (
    select
        channel,
        count(*) * 1.0 / sum(count(*)) over ()      as share
    from {{ ref('fct_order') }}
    where not is_cancelled
    group by channel
),

expected as (
    select * from (values
        ('google',       0.365),
        ('meta',         0.275),
        ('unattributed', 0.269),
        ('tiktok',       0.090)
    ) as t(channel, expected_share)
)

select
    a.channel,
    a.share            as actual_share,
    e.expected_share,
    a.share - e.expected_share as difference
from actual a
inner join expected e on e.channel = a.channel
where abs(a.share - e.expected_share) > 0.02
