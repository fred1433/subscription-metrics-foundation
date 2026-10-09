with ranked as (
    select *, row_number() over (partition by id order by _weld_synced desc) as rn
    from {{ source('shopify', 'refund') }}
    -- known at the cutoff: a version synced after it does not exist yet, older versions stay admissible
    where {{ utc_timestamp('_weld_synced') }} <= {{ as_of() }}
)
select
    {{ to_int('id') }} as refund_id,
    {{ to_int('order_id') }} as shopify_order_id,
    {{ utc_timestamp('created_at') }} as created_at_utc,
    note
from ranked
where rn = 1
