{#
  Cumulative share of a month's budget planned to be spent by the end of
  day `day_col` of a month with `days_col` days, for a pacing profile.

  Cumulative, not daily, on purpose: the pacing mart rounds the CUMULATIVE
  plan to pennies and takes daily spend as the difference, so the daily
  plan always sums to the approved budget exactly. The last day is pinned
  to 1 so float error can never leave a penny over or under.

  linear        Equal spend every day. Daily weight 1/n; cumulative d/n.

  front_loaded  Half linear, half a straight-line decline: daily weight
                  w(d) = 0.5 * 1/n + 0.5 * (2(n - d) + 1) / n^2
                The first day plans ~1.5x an average day and the last ~0.5x;
                about 62% of the budget is planned for the first half of a
                30-day month. Cumulative (closed form, since the sum of
                2(n-k)+1 over k = 1..d is 2nd - d^2):
                  W(d) = 0.5 * d/n + 0.5 * (2nd - d^2) / n^2

  Any other profile returns NULL: an unrecognised profile must not quietly
  pace as linear. accepted_values on stg_marketing_budget.pacing_profile
  rejects it first.
#}
{% macro pacing_cumulative_weight(profile_col, day_col, days_col) %}
    case
        when {{ day_col }} >= {{ days_col }}
             and {{ profile_col }} in ('linear', 'front_loaded')  then 1.0
        when {{ profile_col }} = 'linear'
            then {{ day_col }} * 1.0 / {{ days_col }}
        when {{ profile_col }} = 'front_loaded'
            then 0.5 * {{ day_col }} / {{ days_col }}
               + 0.5 * (2.0 * {{ days_col }} * {{ day_col }} - {{ day_col }} * {{ day_col }})
                     / ({{ days_col }} * {{ days_col }})
    end
{% endmacro %}
