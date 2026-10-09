-- Metabase-ready: trial conversion by cohort month, ratio of sums, with trials still in observation.
-- Compile with `dbt compile` and paste target/.../analyses/metabase_trial_conversion.sql into a native question.
-- (Not run inside Metabase in this repository.)
select
    cohort_month,
    sum(case when is_window_complete and converted_in_window then 1 else 0 end) as converted,
    sum(case when is_window_complete then 1 else 0 end) as trials_with_closed_window,
    1.0 * sum(case when is_window_complete and converted_in_window then 1 else 0 end)
        / nullif(sum(case when is_window_complete then 1 else 0 end), 0) as conversion_rate,
    sum(case when is_window_complete then 0 else 1 end) as trials_still_in_window,
    max(window_days) as window_days
from {{ ref('fct_trials') }}
group by cohort_month
order by cohort_month
