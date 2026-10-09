-- rows the connector delivered more than once for the same full source key
select
    'ad_spend' as check_name,
    platform || '|' || account_id || '|' || campaign_id || '|' || date as item_key,
    'duplicate_connector_row' as category,
    'exception' as classification,
    (count(*) - 1) * max({{ to_pence('spend') }}) as amount_pence,
    0 as order_count_delta,
    cast(count(*) as {{ dbt.type_string() }}) || ' rows for one campaign-day; deduplicated in staging' as note
from {{ source('ads', 'daily_spend') }}
group by platform, account_id, campaign_id, date
having count(*) > 1
