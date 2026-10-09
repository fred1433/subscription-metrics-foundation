-- Fixture F3 (defective scenario): a second trial from the same probable household under another email.
-- Expect an eligibility exception on the later trial only, two customers, and both trial orders kept.
-- In the clean scenario the fixture is absent and the test has nothing to check.
with t as (
    select * from {{ ref('fct_trials') }} where account_id in ('ACC-F3A', 'ACC-F3B')
)
select 'later trial not flagged' as failure
from t where account_id = 'ACC-F3B' and coalesce(eligibility_exception, '') <> 'second_trial_probable_household'
union all
select 'first trial wrongly flagged' from t where account_id = 'ACC-F3A' and eligibility_exception is not null
union all
select 'the two accounts were merged into one customer'
from (select count(*) as n, count(distinct customer_key) as k from t) as x where n > 0 and k <> n
union all
select 'a trial order was dropped'
from (select count(*) as n from {{ ref('fct_orders') }} where platform_order_id in ('PO-F3A-T', 'PO-F3B-T')) as o
where o.n <> (select count(*) from t)
