with ranked as (
    select *, row_number() over (partition by id order by _weld_synced desc) as rn
    from {{ source('shopify', 'transaction') }}
    -- known at the cutoff: a version synced after it does not exist yet, older versions stay admissible
    where {{ utc_timestamp('_weld_synced') }} <= {{ as_of() }}
)
select
    {{ to_int('id') }} as transaction_id,
    {{ to_int('order_id') }} as shopify_order_id,
    {{ to_int('refund_id') }} as refund_id,
    kind,
    status,
    {{ to_pence('amount') }} as amount_pence,
    {{ utc_timestamp('created_at') }} as created_at_utc,
    {{ london_date(utc_timestamp('created_at')) }} as created_date_london,
    {{ utc_timestamp('_weld_synced') }} as synced_at_utc
from ranked
where rn = 1
