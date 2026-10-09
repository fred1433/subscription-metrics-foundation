with ranked as (
    select *, row_number() over (partition by id order by _weld_synced desc) as rn
    from {{ source('shopify', 'order') }}
)
select
    {{ to_int('id') }} as shopify_order_id,
    {{ to_int('customer_id') }} as shopify_customer_id,
    name as order_name,
    {{ utc_timestamp('created_at') }} as created_at_utc,
    {{ london_date(utc_timestamp('created_at')) }} as order_date_london,
    currency,
    {{ to_pence('subtotal_price') }} as subtotal_pence,
    {{ to_pence('total_discounts') }} as total_discounts_pence,
    {{ to_pence('total_price') }} as total_pence,
    {{ to_pence('current_total_price') }} as current_total_pence,
    source_name,
    source_identifier as platform_order_id,
    tags,
    lower(test) = 'true' as is_test,
    {{ utc_timestamp('_weld_synced') }} as synced_at_utc
from ranked
where rn = 1
