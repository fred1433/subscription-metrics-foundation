{%- set reg = metric_registry() %}
with deps as (
    {%- set rows = [] %}
    {%- for m in reg['metrics'] %}{% for s in m.get('source_dependencies', []) %}{% do rows.append((m['id'], s)) %}{% endfor %}{% endfor %}
    {%- for mid, sid in rows %}
    select {{ sql_str(mid) }} as metric_id, {{ sql_str(sid) }} as source_id
    {% if not loop.last %}union all{% endif %}
    {%- endfor %}
),

checks as (
    {%- set crows = [] %}
    {%- for m in reg['metrics'] %}{% for c in m.get('reconciliation_checks', []) %}{% do crows.append((m['id'], c)) %}{% endfor %}{% endfor %}
    {%- for mid, cid in crows %}
    select {{ sql_str(mid) }} as metric_id, {{ sql_str(cid) }} as check_name
    {% if not loop.last %}union all{% endif %}
    {%- endfor %}
),

stale as (
    select
        d.metric_id,
        string_agg(
            f.source_id || ' last synced ' || coalesce(cast(f.last_synced_at_utc as {{ dbt.type_string() }}), 'never')
            || ' UTC, older than ' || cast(f.max_age_hours as {{ dbt.type_string() }}) || 'h', '; '
        ) as stale_sources
    from deps as d
    inner join {{ ref('source_freshness') }} as f on f.source_id = d.source_id
    where f.freshness = 'stale'
    group by d.metric_id
),

open_exceptions as (
    select c.metric_id, count(*) as open_exceptions
    from checks as c
    inner join {{ ref('rec_exceptions') }} as e on e.check_name = c.check_name and e.classification = 'exception'
    group by c.metric_id
)

select
    r.metric_id,
    case
        when r.status <> 'implemented' then 'contract_only'
        when s.metric_id is not null then 'unavailable'
        else 'available'
    end as availability,
    s.stale_sources,
    coalesce(o.open_exceptions, 0) as open_reconciliation_exceptions,
    {{ as_of() }} as checked_at_utc
from {{ ref('metric_registry') }} as r
left join stale as s on s.metric_id = r.metric_id
left join open_exceptions as o on o.metric_id = r.metric_id
