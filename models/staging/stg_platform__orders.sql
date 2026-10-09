select
    platform_order_id,
    subscription_id,
    account_id,
    order_kind,
    order_status,
    cast(due_date as date) as due_date,
    {{ utc_timestamp('paid_at') }} as paid_at_utc,
    {{ to_int('gross_pence') }} as gross_pence,
    {{ to_int('discount_pence') }} as discount_pence,
    {{ to_int('total_pence') }} as total_pence,
    discount_type,
    motif,
    {{ utc_timestamp('_ingested_at') }} as ingested_at_utc
from {{ source('platform', 'orders') }}
where {{ utc_timestamp('_ingested_at') }} <= {{ as_of() }}
