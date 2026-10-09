{#
  Five separate checks, one row each. Every difference is either explained by a listed item or reported as
  unexplained; nothing is netted across checks.
#}
with items as (
    select * from {{ ref('rec_exceptions') }}
),

item_totals as (
    select
        check_name,
        sum(case when classification = 'explained' then amount_pence else 0 end) as explained_pence,
        sum(case when classification = 'explained' then order_count_delta else 0 end) as explained_count,
        sum(case when classification = 'exception' then 1 else 0 end) as exceptions,
        -- gross unexplained: exceptions that are part of the gap, counted without letting them offset
        sum(case when classification = 'exception' and check_name = 'completeness' then abs(amount_pence) else 0 end)
            as unexplained_gross_pence
    from items
    group by check_name
),

order_count as (
    select
        'order_count' as check_name,
        'orders landed vs commercial orders' as compares,
        'orders' as unit,
        (select count(*) from {{ ref('stg_shopify__orders') }} where not is_test) as source_value,
        (select count(*) from {{ ref('fct_orders') }} where is_commercial) as warehouse_value
),

completeness as (
    select
        'completeness' as check_name,
        'platform paid-orders export vs landed Shopify orders' as compares,
        'pence' as unit,
        (select sum(amount_pence) from {{ ref('stg_source_export__platform_paid_orders') }}
            where paid_at_utc <= {{ as_of() }}) as source_value,
        (select sum(total_pence) from {{ ref('stg_shopify__orders') }}
            where platform_order_id is not null and not is_test and created_at_utc <= {{ as_of() }}) as warehouse_value
),

gross_to_net as (
    select
        'gross_to_net' as check_name,
        'Shopify cash (sales minus paid refunds) vs net revenue mart' as compares,
        'pence' as unit,
        (select sum(case when t.kind = 'sale' and t.status = 'success' then t.amount_pence
                         when t.kind = 'refund' and t.status = 'success' then -t.amount_pence else 0 end)
           from {{ ref('stg_shopify__transactions') }} as t
           inner join {{ ref('fct_orders') }} as o on o.shopify_order_id = t.shopify_order_id
           where o.is_commercial) as source_value,
        (select sum(amount_pence) from {{ ref('fct_revenue_movements') }}) as warehouse_value
),

ad_spend as (
    select
        'ad_spend' as check_name,
        'ad platforms report vs deduplicated spend' as compares,
        'pence' as unit,
        (select sum(spend_pence) from {{ ref('stg_source_export__ads_daily_totals') }}) as source_value,
        (select sum(spend_pence) from {{ ref('fct_ad_spend_daily') }}) as warehouse_value
),

trial_eligibility as (
    select
        'trial_eligibility' as check_name,
        'trial events vs trials in the mart' as compares,
        'trials' as unit,
        (select count(*) from {{ ref('stg_platform__subscription_events') }}
            where event_type = 'trial_purchased' and known_at_utc <= {{ as_of() }}) as source_value,
        (select count(*) from {{ ref('fct_trials') }}) as warehouse_value
),

checks as (
    select * from order_count
    union all select * from completeness
    union all select * from gross_to_net
    union all select * from ad_spend
    union all select * from trial_eligibility
),

bridge as (
    -- gross lines - discounts must equal order totals, row by row, before refunds come in
    select sum(gross_pence - discount_pence - original_total_pence) as bridge_residual
    from {{ ref('fct_orders') }}
)

select
    c.check_name,
    c.compares,
    c.unit,
    c.source_value,
    c.warehouse_value,
    c.warehouse_value - c.source_value as difference,
    case when c.unit = 'orders' then coalesce(t.explained_count, 0) else coalesce(t.explained_pence, 0) end
        as explained,
    -- explained items are signed like the difference (warehouse minus source)
    (c.warehouse_value - c.source_value)
        - case
            when c.unit = 'orders' then coalesce(t.explained_count, 0)
            when c.check_name = 'completeness' then coalesce(t.explained_pence, 0)
            else 0
          end
        + case when c.check_name = 'gross_to_net' then (select bridge_residual from bridge) else 0 end
        as unexplained,
    case
        when c.check_name = 'completeness' then coalesce(t.unexplained_gross_pence, 0)
        else abs((c.warehouse_value - c.source_value)
                 - case when c.unit = 'orders' then coalesce(t.explained_count, 0) else 0 end)
    end as unexplained_gross,
    coalesce(t.exceptions, 0) as exceptions,
    case
        when coalesce(t.exceptions, 0) = 0
            and (c.warehouse_value - c.source_value)
                - case when c.unit = 'orders' then coalesce(t.explained_count, 0)
                       when c.check_name = 'completeness' then coalesce(t.explained_pence, 0) else 0 end = 0
            then 'pass'
        else 'exceptions'
    end as status
from checks as c
left join item_totals as t on t.check_name = c.check_name
