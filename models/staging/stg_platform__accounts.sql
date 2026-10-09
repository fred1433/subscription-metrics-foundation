select
    account_id,
    email,
    {{ utc_timestamp('created_at') }} as created_at_utc,
    delivery_address_line1,
    delivery_postcode,
    payment_fingerprint,
    {{ to_int('shopify_customer_id') }} as shopify_customer_id,
    referred_by_account_id,
    {{ utc_timestamp('_ingested_at') }} as ingested_at_utc
from {{ source('platform', 'accounts') }}
