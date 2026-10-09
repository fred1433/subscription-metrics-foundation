select
    platform_order_id,
    {{ to_int('amount_pence') }} as amount_pence,
    currency,
    {{ utc_timestamp('paid_at') }} as paid_at_utc,
    {{ utc_timestamp('exported_at') }} as exported_at_utc
from {{ source('source_export', 'platform_paid_orders') }}
