{%- set reg = metric_registry() %}
with last_seen as (
    select 'platform' as source_id, max(ingested_at_utc) as last_synced_at_utc
    from (
        select ingested_at_utc from {{ ref('stg_platform__subscription_events') }}
        union all
        select ingested_at_utc from {{ ref('stg_platform__orders') }}
    ) as p
    where ingested_at_utc <= {{ as_of() }}
    union all
    select 'shopify', max(synced_at_utc)
    from {{ ref('stg_shopify__orders') }}
    where synced_at_utc <= {{ as_of() }}
    union all
    select 'ads', max(synced_at_utc)
    from {{ ref('stg_ads__daily_spend') }}
    where synced_at_utc <= {{ as_of() }}
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
