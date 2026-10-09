with ranked as (
    select *, row_number() over (partition by id order by _weld_synced desc) as rn
    from {{ source('shopify', 'order_line_items') }}
    -- known at the cutoff: a version synced after it does not exist yet, older versions stay admissible
    where {{ utc_timestamp('_weld_synced') }} <= {{ as_of() }}
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
