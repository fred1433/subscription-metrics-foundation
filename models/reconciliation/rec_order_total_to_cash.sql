-- items beside the gross-to-net bridge: refunds recorded but not paid out, and card checks
with pending_refunds as (
    select
        coalesce(o.platform_order_id, o.order_key) || ':refund:' || cast(t.created_date_london as {{ dbt.type_string() }})
            as item_key,
        'refund_recorded_not_paid' as category,
        'explained' as classification,
        t.amount_pence,
        'refund transaction status ' || t.status || ': not deducted from net revenue yet' as note
    from {{ ref('stg_shopify__transactions') }} as t
    inner join {{ ref('fct_orders') }} as o on o.shopify_order_id = t.shopify_order_id
    where t.kind = 'refund' and t.status <> 'success'
),

checks as (
    select * from {{ ref('stg_platform__payment_transactions') }} where kind = 'card_check'
),

reversals as (
    select * from {{ ref('stg_platform__payment_transactions') }} where kind = 'card_check_reversal'
),

card_checks as (
    select
        c.account_id || ':card_check:' || cast(c.created_date_london as {{ dbt.type_string() }}) as item_key,
        case when r.transaction_id is not null then 'card_check_reversed' else 'card_check_not_reversed' end
            as category,
        case when r.transaction_id is not null then 'explained' else 'exception' end as classification,
        c.amount_pence,
        'card verification, never revenue' as note
    from checks as c
    -- a reversal counts only if it actually succeeded; a parent id and an opposite amount are not enough
    left join reversals as r
        on r.parent_transaction_id = c.transaction_id and r.amount_pence = -c.amount_pence and r.status = 'succeeded'
)

select 'order_total_to_cash' as check_name, item_key, category, classification, amount_pence, 0 as order_count_delta, note
from pending_refunds
union all
select 'order_total_to_cash', item_key, category, classification, amount_pence, 0, note
from card_checks
