{#
  Ad rows sync exactly one day after their event date -- measured, not
  assumed: +1d on 100% of both meta_ads_daily (2,178 rows) and
  google_ads_daily (1,825 rows).

  The backtest depends on that uniformity: at cursor D it treats a day's
  spend as knowable when spend_available_on <= D. If a source ever lands
  same-day or three days late, the reconstruction stops matching a real
  rebuild -- silently, because the numbers would still look plausible.

  Fails on any row whose spend exists but did not arrive exactly one day
  after the trading day it describes.
#}

select
    date_day,
    channel,
    spend_available_on,
    date_diff('day', date_day, spend_available_on) as actual_lag_days
from {{ ref('mart_daily_trading') }}
where ad_spend is not null
  and spend_available_on != date_day + 1
