{#- One marker per feed, so a stopped feed cannot hide behind a live one. -#}
{%- set reg = metric_registry() %}
with last_seen as (
    select 'platform_events' as source_id, max(ingested_at_utc) as last_synced_at_utc
    from {{ ref('stg_platform__subscription_events') }}
    where ingested_at_utc <= {{ as_of() }}
    union all
    select 'platform_orders', max(ingested_at_utc)
    from {{ ref('stg_platform__orders') }}
    union all
    select 'shopify_orders', max(synced_at_utc)
    from {{ ref('stg_shopify__orders') }}
    union all
    select 'shopify_refunds', max(synced_at_utc)
    from {{ ref('stg_shopify__transactions') }}
    where kind = 'refund'
    union all
    select 'ads_spend', max(synced_at_utc)
    from {{ ref('stg_ads__daily_spend') }}
),

thresholds as (
    {%- for s in reg['sources'] %}
    select {{ sql_str(s['id']) }} as source_id, {{ s['max_age_hours'] }} as max_age_hours
    {% if not loop.last %}union all{% endif %}
    {%- endfor %}
)

select
    t.source_id,
    l.last_synced_at_utc,
    {{ as_of() }} as as_of_utc,
    {{ dbt.datediff('l.last_synced_at_utc', as_of(), 'minute') }} / 60.0 as age_hours,
    t.max_age_hours,
    case
        when l.last_synced_at_utc is null then 'stale'
        when {{ dbt.datediff('l.last_synced_at_utc', as_of(), 'minute') }} > t.max_age_hours * 60 then 'stale'
        else 'fresh'
    end as freshness
from thresholds as t
left join last_seen as l on l.source_id = t.source_id
