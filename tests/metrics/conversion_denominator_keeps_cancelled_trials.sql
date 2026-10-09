-- The denominator is every trial whose 42-day window closed, recounted here from raw events,
-- including trials cancelled before the first box. Nothing is filtered out silently.
with raw_trials as (
    select count(*) as n
    from {{ ref('stg_platform__subscription_events') }}
    where event_type = 'trial_purchased'
      and {{ ts_add_days('event_at_utc', var('trial_conversion_window_days')) }} <= {{ as_of() }}
),
served as (
    select sum(denominator) as n
    from {{ ref('metric_values') }}
    where metric_id = 'trial_conversion_rate' and slice_name = 'total'
),
cancelled as (
    select count(*) as n from {{ ref('fct_trials') }} where is_window_complete and cancelled_before_first_box
)
select 'denominator differs from raw trial count' as failure
from raw_trials, served where raw_trials.n <> served.n
union all
select 'no cancelled trial in the denominator' from cancelled where n = 0
