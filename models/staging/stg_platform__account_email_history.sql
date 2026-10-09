select
    account_id,
    email,
    {{ utc_timestamp('valid_from') }} as valid_from_utc,
    {{ utc_timestamp('valid_to') }} as valid_to_utc
from {{ source('platform', 'account_email_history') }}
