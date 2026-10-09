-- active_subscriptions counts PAYING subscriptions: the first box must be paid as known at the month end.
-- Fixture F12: trial 17/04, first box fails 30/04 (payment_retry at the end of April), paid 08/05.
with served as (
    select period, numerator from {{ ref('metric_values') }}
    where metric_id = 'active_subscriptions' and slice_name = 'total'
),
recount as (
    select s.state_date, count(*) as n
    from {{ ref('fct_subscription_state_daily') }} as s
    where s.subscription_state in ('active', 'payment_retry') and s.first_box_paid_known
    group by s.state_date
)
select 'served count differs from paying subscriptions at month end' as failure, cast(v.period as {{ dbt.type_string() }}) as detail
from served as v
inner join (
    select {{ month_start('state_date') }} as period, max(state_date) as month_end
    from {{ ref('fct_subscription_state_daily') }} group by {{ month_start('state_date') }}
) as m on m.period = v.period
left join recount as r on r.state_date = m.month_end
where v.numerator <> coalesce(r.n, 0)
union all
select 'F12 counted as paying at the end of April', null
from {{ ref('fct_active_subscriptions_month_end') }}
where subscription_id = 'SUB-F12' and period = cast('2026-04-01' as date)
union all
select 'F12 missing at the end of May', null
from (select count(*) as n from {{ ref('fct_active_subscriptions_month_end') }}
      where subscription_id = 'SUB-F12' and period = cast('2026-05-01' as date)) as x
where n <> 1
