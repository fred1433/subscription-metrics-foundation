-- Central journey (synthetic dates, J0 = 2026-06-01):
--   J0 trial paid, J5 renewal moved from J9 to J20, J6 blank order on a card update,
--   J20 first box payment fails, J23 retry succeeds.
-- J19: no use of the future success. J21: the failure is not a bought box and not churn. J23: conversion recorded.
with expected as (
    select cast('2026-06-20' as date) as state_date, 'trial_pending_first_box' as subscription_state,
           false as first_box_paid_known, cast('2026-06-21' as date) as expected_renewal_date
    union all
    select cast('2026-06-22' as date), 'payment_retry', false, cast('2026-06-21' as date)
    union all
    select cast('2026-06-24' as date), 'active', true, cast('2026-07-19' as date)
),
actual as (
    select * from {{ ref('fct_subscription_state_daily') }} where subscription_id = 'SUB-J'
)
select e.state_date, e.subscription_state as expected_state, a.subscription_state as actual_state
from expected as e
left join actual as a on a.state_date = e.state_date
where a.subscription_state is null
   or a.subscription_state <> e.subscription_state
   or a.first_box_paid_known <> e.first_box_paid_known
   or a.expected_renewal_date <> e.expected_renewal_date
union all
select null, 'converted within 42 days', 'not converted'
from {{ ref('fct_trials') }}
where subscription_id = 'SUB-J' and not (converted_in_window and cast(first_box_paid_at_utc as date) = cast('2026-06-24' as date))
union all
select null, 'blank order not commercial', order_kind
from {{ ref('fct_orders') }}
where platform_order_id = 'PO-J-BLANK' and (is_commercial or order_kind <> 'blank_payment_update')
