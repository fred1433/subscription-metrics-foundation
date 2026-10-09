-- sales on the order date, refunds on the date the money actually left; blank orders excluded
with orders as (
    select * from {{ ref('fct_orders') }} where is_commercial
)

select
    order_key || ':sale' as movement_id,
    order_key,
    platform_order_id,
    customer_key,
    order_kind,
    'sale' as movement_type,
    created_at_utc as movement_at_utc,
    order_date_london as movement_date_london,
    {{ month_start('order_date_london') }} as movement_month,
    original_total_pence as amount_pence
from orders

union all

select
    'shopify_transaction:' || cast(t.transaction_id as {{ dbt.type_string() }}) as movement_id,
    o.order_key,
    o.platform_order_id,
    o.customer_key,
    o.order_kind,
    'refund' as movement_type,
    t.created_at_utc as movement_at_utc,
    t.created_date_london as movement_date_london,
    {{ month_start('t.created_date_london') }} as movement_month,
    -t.amount_pence as amount_pence
from {{ ref('stg_shopify__transactions') }} as t
inner join orders as o on o.shopify_order_id = t.shopify_order_id
where t.kind = 'refund' and t.status = 'success'
