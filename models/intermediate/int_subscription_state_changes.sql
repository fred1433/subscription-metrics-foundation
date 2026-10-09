{#
  One row per known event, carrying the subscription's cumulative state right after it.
  Order is the moment the warehouse could know the event (max of event time and ingestion time),
  so a state on a given day never uses information that arrived later.
#}
with ev as (
    select
        *,
        row_number() over (partition by subscription_id order by known_at_utc, event_id) as seq
    from {{ ref('stg_platform__subscription_events') }}
    where known_at_utc <= {{ as_of() }}
)

select
    subscription_id,
    event_id,
    event_type,
    event_at_utc,
    known_at_utc,
    {{ london_date('known_at_utc') }} as known_date_london,
    seq,
    last_value(
        case
            when event_type in ('trial_purchased', 'subscription_enrolled', 'resumed') then 'active'
            when event_type = 'paused' then 'paused'
            when event_type = 'cancelled' then 'cancelled'
        end ignore nulls
    ) over w as lifecycle_status,
    last_value(
        case when event_type in ('payment_failed', 'payment_succeeded', 'payment_retry_exhausted') then event_type end
        ignore nulls
    ) over w as last_payment_outcome,
    last_value(case when event_type = 'payment_failed' then event_at_utc end ignore nulls) over w
        as last_payment_failed_at_utc,
    min(case when event_type = 'first_subscription_box_paid' then event_at_utc end) over w
        as first_box_paid_at_utc,
    last_value(next_renewal_date ignore nulls) over w as expected_renewal_date
from ev
window w as (partition by subscription_id order by seq rows between unbounded preceding and current row)
