-- a later trial in a PROBABLE household is an eligibility exception; the sale is real and is kept
with trials as (
    select
        e.platform_order_id as trial_order_id,
        e.event_at_utc as trial_at_utc,
        s.account_id
    from {{ ref('stg_platform__subscription_events') }} as e
    inner join {{ ref('stg_platform__subscriptions') }} as s on s.subscription_id = e.subscription_id
    where e.event_type = 'trial_purchased' and e.known_at_utc <= {{ as_of() }}
),

in_household as (
    select
        t.trial_order_id,
        t.trial_at_utc,
        h.household_id,
        row_number() over (partition by h.household_id order by t.trial_at_utc) as trial_seq_in_household
    from trials as t
    inner join {{ ref('int_probable_households') }} as h
        on h.account_id = t.account_id and h.shares_payment_fingerprint
)

select
    t.trial_order_id,
    i.household_id,
    i.trial_seq_in_household,
    case when i.trial_seq_in_household > 1 then 'second_trial_probable_household' end as eligibility_exception
from trials as t
left join in_household as i on i.trial_order_id = t.trial_order_id
