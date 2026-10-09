-- the connector can deliver a record again; the latest sync wins
with ranked as (
    select *, row_number() over (partition by id order by _weld_synced desc) as rn
    from {{ source('shopify', 'customer') }}
    -- known at the cutoff: a version synced after it does not exist yet, older versions stay admissible
    where {{ utc_timestamp('_weld_synced') }} <= {{ as_of() }}
)
select
    {{ to_int('id') }} as shopify_customer_id,
    email,
    {{ utc_timestamp('created_at') }} as created_at_utc,
    {{ utc_timestamp('_weld_synced') }} as synced_at_utc
from ranked
where rn = 1
