-- A subscription is in state 'cancelled' only if a cancellation was known by that day.
-- Moved renewal dates, skips, expedited orders, failed payments and exhausted retries never produce it.
with cancels as (
    select subscription_id, min(known_at_utc) as first_cancel_known_at
    from {{ ref('stg_platform__subscription_events') }}
    where event_type = 'cancelled'
    group by subscription_id
)
select s.subscription_id, s.state_date
from {{ ref('fct_subscription_state_daily') }} as s
left join cancels as c on c.subscription_id = s.subscription_id
where s.subscription_state = 'cancelled'
  and (c.first_cancel_known_at is null or {{ london_date('c.first_cancel_known_at') }} > s.state_date)
union all
-- an unpaid order cancelled after the retries leaves the subscription alive the next day
select o.subscription_id, o.due_date
from {{ ref('stg_platform__orders') }} as o
inner join {{ ref('fct_subscription_state_daily') }} as s
    on s.subscription_id = o.subscription_id
    and s.state_date = {{ date_add_days('o.due_date', 31) }}
left join cancels as c on c.subscription_id = o.subscription_id
where o.order_status = 'unpaid_cancelled' and s.subscription_state = 'cancelled' and c.subscription_id is null
