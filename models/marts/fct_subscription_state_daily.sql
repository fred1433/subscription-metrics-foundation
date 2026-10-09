{#
  The state of every subscription on every day, as it was known at the end of that day.
    cancelled                 a cancellation was the last lifecycle event
    paused                    a pause was the last lifecycle event
    payment_retry             the last payment failed less than 30 days ago (public terms 7.6): not churn
    trial_pending_first_box   no subscription box paid yet
    active                    otherwise
  A moved renewal date (Change Date, skip, "I need it now") changes expected_renewal_date, never the state.
  An unpaid order cancelled after 30 days of retries cancels the order, not the subscription.
#}
with changes as (
    select * from {{ ref('int_subscription_state_changes') }}
),

end_of_day as (
    select *
    from (
        select *, row_number() over (partition by subscription_id, known_date_london order by seq desc) as rn
        from changes
    ) as x
    where rn = 1
),

intervals as (
    select
        *,
        lead(known_date_london) over (partition by subscription_id order by known_date_london) as valid_to_date
    from end_of_day
),

days as (
    {{ day_series("'2025-04-01'", as_of_date()) }}
)

select
    i.subscription_id,
    d.day as state_date,
    case
        when i.lifecycle_status = 'cancelled' then 'cancelled'
        when i.lifecycle_status = 'paused' then 'paused'
        when i.last_payment_outcome = 'payment_failed'
            and {{ dbt.datediff(london_date('i.last_payment_failed_at_utc'), 'd.day', 'day') }} <= {{ var('payment_retry_days') }}
            then 'payment_retry'
        when i.first_box_paid_at_utc is null then 'trial_pending_first_box'
        else 'active'
    end as subscription_state,
    i.first_box_paid_at_utc is not null as first_box_paid_known,
    i.first_box_paid_at_utc,
    i.expected_renewal_date,
    i.last_payment_outcome
from intervals as i
inner join days as d
    on d.day >= i.known_date_london
    and (i.valid_to_date is null or d.day < i.valid_to_date)
