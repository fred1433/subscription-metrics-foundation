-- Fixture F13: pause effective 01/08 ingested 03/08, resume effective and ingested 02/08.
-- Among known events, the latest EFFECTIVE one wins: active on 02/08 and still active on 03/08.
select state_date, subscription_state
from {{ ref('fct_subscription_state_daily') }}
where subscription_id = 'SUB-F13'
  and state_date in (cast('2026-08-02' as date), cast('2026-08-03' as date))
  and subscription_state <> 'active'
union all
select cast('2026-08-03' as date), 'missing day'
from (select count(*) as n from {{ ref('fct_subscription_state_daily') }}
      where subscription_id = 'SUB-F13' and state_date = cast('2026-08-03' as date)) as x
where n <> 1
