{#
  Six separate checks, one row each. Every difference is either explained by a listed item or reported as
  unexplained; nothing is netted across checks.
    difference          warehouse minus source, signed
    unexplained         the signed part of the difference that no explained item accounts for
    unexplained_gross   the same, counted without letting items offset each other (sum of absolute values)
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
        sum(case when classification = 'exception' then abs(amount_pence) else 0 end) as exception_abs_pence
    from items
    group by check_name
),

checks as (
    select
        'order_count' as check_name, 'orders' as unit,
        (select count(*) from {{ ref('stg_shopify__orders') }} where not is_test) as source_value,
        (select count(*) from {{ ref('fct_orders') }} where is_commercial) as warehouse_value
    union all
    select
        'completeness', 'pence',
        (select sum(amount_pence) from {{ ref('stg_source_export__platform_paid_orders') }}
            where paid_at_utc <= {{ as_of() }}),
        (select sum(total_pence) from {{ ref('stg_shopify__orders') }}
            where platform_order_id is not null and not is_test and created_at_utc <= {{ as_of() }})
    union all
    select
        'lines_to_order_total', 'pence',
        (select sum(gross_pence - discount_pence) from {{ ref('fct_orders') }}),
        (select sum(original_total_pence) from {{ ref('fct_orders') }})
    union all
    select
        'order_total_to_cash', 'pence',
        (select sum(case when t.kind = 'sale' and t.status = 'success' then t.amount_pence
                         when t.kind = 'refund' and t.status = 'success' then -t.amount_pence else 0 end)
           from {{ ref('stg_shopify__transactions') }} as t
           inner join {{ ref('fct_orders') }} as o on o.shopify_order_id = t.shopify_order_id
           where o.is_commercial),
        (select sum(amount_pence) from {{ ref('fct_revenue_movements') }})
    union all
    select
        'ad_spend', 'pence',
        (select sum(spend_pence) from {{ ref('stg_source_export__ads_daily_totals') }}),
        (select sum(spend_pence) from {{ ref('fct_ad_spend_daily') }})
    union all
    select
        'trial_eligibility', 'trials',
        (select count(*) from {{ ref('stg_platform__subscription_events') }}
            where event_type = 'trial_purchased' and known_at_utc <= {{ as_of() }}),
        (select count(*) from {{ ref('fct_trials') }})
),

computed as (
    select
        c.check_name,
        c.unit,
        c.source_value,
        c.warehouse_value,
        c.warehouse_value - c.source_value as difference,
        case when c.unit = 'orders' then coalesce(t.explained_count, 0)
             when c.check_name = 'completeness' then coalesce(t.explained_pence, 0)
             else 0 end as explained,
        coalesce(t.exceptions, 0) as exceptions,
        coalesce(t.exception_abs_pence, 0) as exception_abs_pence
    from checks as c
    left join item_totals as t on t.check_name = c.check_name
)

select
    check_name,
    case check_name
        when 'order_count' then 'orders landed vs commercial orders'
        when 'completeness' then 'platform paid-orders export vs landed Shopify orders'
        when 'lines_to_order_total' then 'order lines minus discounts vs order totals, order by order'
        when 'order_total_to_cash' then 'Shopify cash (sales minus paid refunds) vs net revenue mart'
        when 'ad_spend' then 'ad platforms report vs deduplicated spend'
        else 'trial events vs trials in the mart'
    end as compares,
    unit,
    source_value,
    warehouse_value,
    difference,
    explained,
    difference - explained as unexplained,
    case
        when check_name in ('completeness', 'lines_to_order_total') then exception_abs_pence
        else abs(difference - explained)
    end as unexplained_gross,
    exceptions,
    case when exceptions = 0 and difference - explained = 0 then 'pass' else 'exceptions' end as status
from computed
