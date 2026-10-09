select
    transaction_id,
    account_id,
    platform_order_id,
    kind,
    {{ to_int('amount_pence') }} as amount_pence,
    status,
    {{ utc_timestamp('created_at') }} as created_at_utc,
    {{ london_date(utc_timestamp('created_at')) }} as created_date_london,
    parent_transaction_id
from {{ source('platform', 'payment_transactions') }}
