-- Rule: a subscription whose expected renewal is more than 35 days overdue (28-day cycle + 7 days grace)
-- with nothing known since is 'lapsed': it no longer counts as active.
-- Fixture F11: one box on 10/03/2026, next renewal due 07/04, then silence.
select 'active or retry with a renewal overdue by more than 35 days' as failure, subscription_id, state_date
from {{ ref('fct_subscription_state_daily') }}
where subscription_state in ('active', 'payment_retry', 'trial_pending_first_box')
  and expected_renewal_date < {{ date_add_days('state_date', -35) }}
union all
select 'F11 not lapsed at the cutoff', subscription_id, state_date
from {{ ref('fct_subscription_state_daily') }}
where subscription_id = 'SUB-F11' and state_date = {{ as_of_date() }} and subscription_state <> 'lapsed'
union all
select 'F11 not active before its renewal was due', subscription_id, state_date
from {{ ref('fct_subscription_state_daily') }}
where subscription_id = 'SUB-F11' and state_date = cast('2026-04-01' as date) and subscription_state <> 'active'
