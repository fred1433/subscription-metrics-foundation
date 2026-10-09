-- One row per subscription counted as an active PAYING subscription at a month end (or at the cutoff for
-- the current month): state active or payment_retry, and its first box already paid as known that day.
with month_ends as (
    select {{ month_start('day') }} as period, max(day) as month_end
    from ({{ day_series("'2025-04-01'", as_of_date()) }}) as d
    group by {{ month_start('day') }}
)

select
    m.period,
    m.month_end,
    s.subscription_id,
    sub.cats_on_plan_band,
    s.subscription_state
from {{ ref('fct_subscription_state_daily') }} as s
inner join month_ends as m on m.month_end = s.state_date
inner join {{ ref('stg_platform__subscriptions') }} as sub on sub.subscription_id = s.subscription_id
where s.subscription_state in ('active', 'payment_retry')
  and s.first_box_paid_known
