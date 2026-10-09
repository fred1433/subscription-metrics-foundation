-- the connector can deliver a record again; the latest sync wins
with ranked as (
    select *, row_number() over (partition by id order by _weld_synced desc) as rn
    from {{ source('shopify', 'customer') }}
)
select
    {{ to_int('id') }} as shopify_customer_id,
    email,
    {{ utc_timestamp('created_at') }} as created_at_utc,
    {{ utc_timestamp('_weld_synced') }} as synced_at_utc
from ranked
where rn = 1
