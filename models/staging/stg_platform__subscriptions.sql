select
    subscription_id,
    account_id,
    {{ utc_timestamp('created_at') }} as created_at_utc,
    {{ to_int('cats_on_plan') }} as cats_on_plan,
    case when {{ to_int('cats_on_plan') }} >= 3 then '3+' else cast(cats_on_plan as {{ dbt.type_string() }}) end as cats_on_plan_band,
    {{ to_int('tins_per_day_total') }} as tins_per_day_total
from {{ source('platform', 'subscriptions') }}
