-- The expected renewal date only moves earlier through an explicit Change Date.
with c as (
    select
        subscription_id,
        event_id,
        event_type,
        expected_renewal_date,
        lag(expected_renewal_date) over (partition by subscription_id order by seq) as previous_expected
    from {{ ref('int_subscription_state_changes') }}
)
select subscription_id, event_id, event_type, previous_expected, expected_renewal_date
from c
where expected_renewal_date < previous_expected and event_type <> 'renewal_date_changed'
