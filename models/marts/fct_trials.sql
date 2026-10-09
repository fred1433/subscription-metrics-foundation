-- one row per trial purchase (a paid order), with its declared conversion window
with trials as (
    select subscription_id, platform_order_id as trial_order_id, event_at_utc as trial_at_utc,
           event_date_london as trial_date_london
    from {{ ref('stg_platform__subscription_events') }}
    where event_type = 'trial_purchased' and known_at_utc <= {{ as_of() }}
),

first_box as (
    select subscription_id, min(event_at_utc) as first_box_paid_at_utc
    from {{ ref('stg_platform__subscription_events') }}
    where event_type = 'first_subscription_box_paid' and known_at_utc <= {{ as_of() }}
    group by subscription_id
),

cancelled_in_trial as (
    select distinct e.subscription_id
    from {{ ref('stg_platform__subscription_events') }} as e
    left join first_box as f on f.subscription_id = e.subscription_id
    where e.event_type = 'cancelled' and f.first_box_paid_at_utc is null
),

base as (
    select
        t.*,
        s.account_id,
        s.cats_on_plan,
        s.cats_on_plan_band,
        a.referred_by_account_id is not null as is_referred,
        l.customer_key,
        f.first_box_paid_at_utc,
        {{ ts_add_days('t.trial_at_utc', var('trial_conversion_window_days')) }} as window_end_at_utc,
        c.subscription_id is not null as cancelled_before_first_box
    from trials as t
    inner join {{ ref('stg_platform__subscriptions') }} as s on s.subscription_id = t.subscription_id
    inner join {{ ref('stg_platform__accounts') }} as a on a.account_id = s.account_id
    inner join {{ ref('int_identity_links') }} as l on l.source_system = 'platform' and l.source_id = s.account_id
    left join first_box as f on f.subscription_id = t.subscription_id
    left join cancelled_in_trial as c on c.subscription_id = t.subscription_id
)

select
    b.trial_order_id,
    b.subscription_id,
    b.account_id,
    b.customer_key,
    b.trial_at_utc,
    b.trial_date_london,
    {{ month_start('b.trial_date_london') }} as cohort_month,
    b.cats_on_plan,
    b.cats_on_plan_band,
    b.is_referred,
    {{ var('trial_conversion_window_days') }} as window_days,
    b.window_end_at_utc,
    b.window_end_at_utc <= {{ as_of() }} as is_window_complete,
    b.first_box_paid_at_utc,
    b.first_box_paid_at_utc is not null and b.first_box_paid_at_utc <= b.window_end_at_utc as converted_in_window,
    b.cancelled_before_first_box,
    e.eligibility_exception
from base as b
left join {{ ref('int_trial_eligibility') }} as e on e.trial_order_id = b.trial_order_id
