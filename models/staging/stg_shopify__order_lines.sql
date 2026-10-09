with ranked as (
    select *, row_number() over (partition by id order by _weld_synced desc) as rn
    from {{ source('shopify', 'order_line_items') }}
)
select
    {{ to_int('id') }} as order_line_id,
    {{ to_int('order_id') }} as shopify_order_id,
    sku,
    title,
    {{ to_int('quantity') }} as quantity,
    {{ to_pence('price') }} as price_pence,
    {{ to_int('quantity') }} * {{ to_pence('price') }} as line_gross_pence
from ranked
where rn = 1
