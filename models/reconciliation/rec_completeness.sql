{#
  Source export (platform, paid orders) against the landing layer (Shopify orders carrying a platform id).
  Keys missing or unexpected, amounts per key, same currency, same cutoff, and a tolerated arrival delay.
  A scheduled order that has not been charged is not missing revenue.
#}
with src as (
    select * from {{ ref('stg_source_export__platform_paid_orders') }}
    where paid_at_utc <= {{ as_of() }}
),

landed as (
    select
        platform_order_id,
        count(*) as landed_orders,
        sum(total_pence) as landed_pence,
        min(currency) as currency
    from {{ ref('stg_shopify__orders') }}
    where platform_order_id is not null and not is_test and created_at_utc <= {{ as_of() }}
    group by platform_order_id
),

platform as (
    select * from {{ ref('stg_platform__orders') }}
),

missing as (
    select
        s.platform_order_id as item_key,
        case
            when s.paid_at_utc > {{ ts_add_hours(as_of(), -var('arrival_tolerance_hours')) }}
                then 'within_arrival_tolerance'
            else 'missing_in_landing'
        end as category,
        -s.amount_pence as amount_pence,
        'paid ' || cast(s.paid_at_utc as {{ dbt.type_string() }}) || ' UTC, not in the landing layer' as note
    from src as s
    left join landed as l on l.platform_order_id = s.platform_order_id
    where l.platform_order_id is null
),

not_in_source as (
    select
        l.platform_order_id as item_key,
        case
            when p.order_kind = 'blank_payment_update' then 'non_commercial_blank_order'
            else 'unexpected_in_landing'
        end as category,
        l.landed_pence as amount_pence,
        'landed but absent from the paid-orders export' as note
    from landed as l
    left join src as s on s.platform_order_id = l.platform_order_id
    left join platform as p on p.platform_order_id = l.platform_order_id
    where s.platform_order_id is null
),

matched as (
    select
        s.platform_order_id as item_key,
        case
            when l.landed_orders > 1 then 'duplicate_in_landing'
            when l.currency <> s.currency then 'currency_mismatch'
            else 'amount_mismatch'
        end as category,
        l.landed_pence - s.amount_pence as amount_pence,
        cast(l.landed_orders as {{ dbt.type_string() }}) || ' landed order(s) for one paid order' as note
    from src as s
    inner join landed as l on l.platform_order_id = s.platform_order_id
    where l.landed_orders > 1 or l.landed_pence <> s.amount_pence or l.currency <> s.currency
),

scheduled as (
    select
        platform_order_id as item_key,
        'scheduled_not_charged' as category,
        cast(0 as {{ dbt.type_bigint() }}) as amount_pence,
        'due ' || cast(due_date as {{ dbt.type_string() }}) || ', not charged at the cutoff: not missing revenue' as note
    from platform
    where order_status = 'scheduled' and due_date <= {{ as_of_date() }}
),

items as (
    select * from missing
    union all select * from not_in_source
    union all select * from matched
    union all select * from scheduled
)

select
    'completeness' as check_name,
    item_key,
    category,
    case
        when category in ('within_arrival_tolerance', 'non_commercial_blank_order', 'scheduled_not_charged')
            then 'explained'
        else 'exception'
    end as classification,
    amount_pence,
    0 as order_count_delta,
    note
from items
