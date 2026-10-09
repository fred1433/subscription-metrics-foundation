-- Fixture F14: box due 24/07 fails (ingested only on 30/07), retry succeeds 26/07 (known at once).
-- When the old failure becomes known, the newer success still wins: never back in payment_retry.
select state_date, subscription_state
from {{ ref('fct_subscription_state_daily') }}
where subscription_id = 'SUB-F14'
  and state_date between cast('2026-07-26' as date) and cast('2026-08-05' as date)
  and subscription_state <> 'active'
