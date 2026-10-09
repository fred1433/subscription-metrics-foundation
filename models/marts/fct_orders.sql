{#
  One row per Shopify order. Kind and motif come from the platform order the Shopify order points to
  (assumption: the platform writes its order id into source_identifier when it pushes an order).
  Classification is by motif, never by amount: a blank order and a free replacement are both 0,
  only the blank one leaves the commercial order count.
#}
with o as (
    select * from {{ ref('stg_shopify__orders') }} where not is_test
),

p as (
    select * from {{ ref('stg_platform__orders') }}
),

lines as (
    select shopify_order_id, sum(line_gross_pence) as gross_pence, count(*) as line_count
    from {{ ref('stg_shopify__order_lines') }}
    group by shopify_order_id
),

refunds as (
    select
        shopify_order_id,
        sum(amount_pence) as refunds_recorded_pence,
        sum(case when status = 'success' then amount_pence else 0 end) as refunds_paid_pence
    from {{ ref('stg_shopify__transactions') }}
    where kind = 'refund'
    group by shopify_order_id
),

links as (
    select * from {{ ref('int_identity_links') }} where source_system = 'shopify'
)

select
    'shopify:' || cast(o.shopify_order_id as {{ dbt.type_string() }}) as order_key,
    o.shopify_order_id,
    o.order_name,
    o.platform_order_id,
    l.customer_key,
    o.created_at_utc,
    o.order_date_london,
    {{ month_start('o.order_date_london') }} as order_month,
    case
        when p.platform_order_id is not null then p.order_kind
        when o.platform_order_id is not null then 'subscription_unmatched'
        else 'one_off'
    end as order_kind,
    coalesce(p.motif, '') as order_motif,
    coalesce(p.order_kind, '') <> 'blank_payment_update' as is_commercial,
    coalesce(lines.line_count, 0) as line_count,
    coalesce(lines.gross_pence, 0) as gross_pence,
    o.total_discounts_pence as discount_pence,
    o.total_pence as original_total_pence,
    o.current_total_pence,
    coalesce(refunds.refunds_recorded_pence, 0) as refunds_recorded_pence,
    coalesce(refunds.refunds_paid_pence, 0) as refunds_paid_pence,
    o.total_pence - coalesce(refunds.refunds_paid_pence, 0) as net_revenue_pence,
    o.currency
from o
left join p on p.platform_order_id = o.platform_order_id
left join lines on lines.shopify_order_id = o.shopify_order_id
left join refunds on refunds.shopify_order_id = o.shopify_order_id
left join links as l on l.source_id = cast(o.shopify_customer_id as {{ dbt.type_string() }})
