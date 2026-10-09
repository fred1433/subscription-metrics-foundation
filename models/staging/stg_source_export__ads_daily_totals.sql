select
    platform,
    cast(date as date) as spend_date_london,
    {{ to_int('spend_pence') }} as spend_pence
from {{ source('source_export', 'ads_platform_daily_totals') }}
