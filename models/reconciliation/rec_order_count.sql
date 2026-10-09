-- items that explain the gap between orders landed and commercial orders
select
    'order_count' as check_name,
    coalesce(platform_order_id, order_key) as item_key,
    case
        when order_kind = 'blank_payment_update' then 'non_commercial_blank_order'
        else 'zero_price_replacement_kept'
    end as category,
    'explained' as classification,
    cast(0 as {{ dbt.type_bigint() }}) as amount_pence,
    case when order_kind = 'blank_payment_update' then -1 else 0 end as order_count_delta,
    'motif: ' || order_motif as note
from {{ ref('fct_orders') }}
where order_kind in ('blank_payment_update', 'replacement')
