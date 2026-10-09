-- Fixture F2: flatmates at the same address (written differently), different emails and cards.
-- Expect 2 customers, 1 possible household, and no trial eligibility exception.
with c as (
    select * from {{ ref('dim_customers') }} where platform_account_id in ('ACC-F2A', 'ACC-F2B')
),
h as (
    select * from {{ ref('int_probable_households') }} where account_id in ('ACC-F2A', 'ACC-F2B')
)
select 'expected 2 customers' as failure from (select count(distinct customer_key) as n from c) as x where n <> 2
union all
select 'expected both in one household' from (select count(distinct household_id) as n, count(*) as m from h) as x
where n <> 1 or m <> 2
union all
select 'household should be possible, not probable' from h where confidence <> 'possible'
union all
select 'flatmate trial wrongly flagged'
from {{ ref('fct_trials') }}
where account_id in ('ACC-F2A', 'ACC-F2B') and eligibility_exception is not null
