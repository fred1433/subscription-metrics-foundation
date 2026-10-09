{#
  One row per known event: the subscription's state as it can be stated at the moment that event became known.
  Two clocks, applied in this order:
    1. knowledge: only events already known at the observation (known_at = max(event time, ingestion time));
    2. effect: among those, each component (lifecycle status, last payment outcome, expected renewal date)
       comes from the event with the latest EFFECTIVE time (event_at_utc).
  Tie-break when two events share the same effective time: the higher event_id wins (source emission order).
  So a pause effective 01/08 but ingested 03/08 does not override a resume effective 02/08, and an old
  payment failure ingested late does not override a newer success.
#}
with ev as (
    select
        *,
        row_number() over (partition by subscription_id order by known_at_utc, event_id) as seq
    from {{ ref('stg_platform__subscription_events') }}
    where known_at_utc <= {{ as_of() }}
),

pairs as (
    -- every observation paired with the events known by then (same instant: by known order)
    select
        o.subscription_id,
        o.seq as obs_seq,
        e.event_id,
        e.event_type,
        e.event_at_utc,
        e.next_renewal_date
    from ev as o
    inner join ev as e
        on e.subscription_id = o.subscription_id
        and e.seq <= o.seq
),

lifecycle as (
    select subscription_id, obs_seq, lifecycle_status
    from (
        select
            subscription_id,
            obs_seq,
            case
                when event_type in ('trial_purchased', 'subscription_enrolled', 'resumed') then 'active'
                when event_type = 'paused' then 'paused'
                else 'cancelled'
            end as lifecycle_status,
            row_number() over (partition by subscription_id, obs_seq order by event_at_utc desc, event_id desc) as rn
        from pairs
        where event_type in ('trial_purchased', 'subscription_enrolled', 'resumed', 'paused', 'cancelled')
    ) as x
    where rn = 1
),

payment as (
    select subscription_id, obs_seq, last_payment_outcome, last_payment_event_at_utc
    from (
        select
            subscription_id,
            obs_seq,
            event_type as last_payment_outcome,
            event_at_utc as last_payment_event_at_utc,
            row_number() over (partition by subscription_id, obs_seq order by event_at_utc desc, event_id desc) as rn
        from pairs
        where event_type in ('payment_failed', 'payment_succeeded', 'payment_retry_exhausted')
    ) as x
    where rn = 1
),

renewal as (
    select subscription_id, obs_seq, expected_renewal_date
    from (
        select
            subscription_id,
            obs_seq,
            next_renewal_date as expected_renewal_date,
            row_number() over (partition by subscription_id, obs_seq order by event_at_utc desc, event_id desc) as rn
        from pairs
        where next_renewal_date is not null
    ) as x
    where rn = 1
),

first_box as (
    select subscription_id, obs_seq, min(event_at_utc) as first_box_paid_at_utc
    from pairs
    where event_type = 'first_subscription_box_paid'
    group by subscription_id, obs_seq
)

select
    ev.subscription_id,
    ev.event_id,
    ev.event_type,
    ev.event_at_utc,
    ev.known_at_utc,
    {{ london_date('ev.known_at_utc') }} as known_date_london,
    ev.seq,
    l.lifecycle_status,
    p.last_payment_outcome,
    case when p.last_payment_outcome = 'payment_failed' then p.last_payment_event_at_utc end
        as last_payment_failed_at_utc,
    f.first_box_paid_at_utc,
    r.expected_renewal_date
from ev
left join lifecycle as l on l.subscription_id = ev.subscription_id and l.obs_seq = ev.seq
left join payment as p on p.subscription_id = ev.subscription_id and p.obs_seq = ev.seq
left join renewal as r on r.subscription_id = ev.subscription_id and r.obs_seq = ev.seq
left join first_box as f on f.subscription_id = ev.subscription_id and f.obs_seq = ev.seq
