with src as (
    select
        event_id,
        subscription_id,
        event_type,
        {{ utc_timestamp('event_at') }} as event_at_utc,
        {{ utc_timestamp('ingested_at') }} as ingested_at_utc,
        platform_order_id,
        cast(next_renewal_date as date) as next_renewal_date,
        cast(previous_renewal_date as date) as previous_renewal_date,
        reason
    from {{ source('platform', 'subscription_events') }}
)

select
    *,
    -- an event can only shape a state from the moment the warehouse could know it
    case when ingested_at_utc > event_at_utc then ingested_at_utc else event_at_utc end as known_at_utc,
    {{ london_date('event_at_utc') }} as event_date_london
from src
