{#
  Quality policy (declared in the registry, served as-is by the MCP server):
    unavailable  a freshness marker the metric depends on is stale: no number is served
    degraded     a reconciliation check linked to the metric has failed: served with the named exceptions
    available    otherwise
#}
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

-- every failed linked check counts: listed exceptions AND any check whose summary is not 'pass'
failed as (
    select c.metric_id, e.check_name || ':' || e.item_key || ' (' || e.category || ')' as failure
    from checks as c
    inner join {{ ref('rec_exceptions') }} as e on e.check_name = c.check_name and e.classification = 'exception'
    union all
    select c.metric_id, s.check_name || ': ' || cast(s.unexplained as {{ dbt.type_string() }}) || ' unexplained'
    from checks as c
    inner join {{ ref('rec_summary') }} as s on s.check_name = c.check_name
    where s.status <> 'pass' and s.exceptions = 0
),

failed_agg as (
    select metric_id, count(*) as failed_items, string_agg(failure, '; ') as failures
    from failed
    group by metric_id
)

select
    r.metric_id,
    case
        when r.status <> 'implemented' then 'contract_only'
        when s.metric_id is not null then 'unavailable'
        when f.metric_id is not null then 'degraded'
        else 'available'
    end as availability,
    s.stale_sources,
    coalesce(f.failed_items, 0) as open_reconciliation_exceptions,
    f.failures as reconciliation_failures,
    {{ as_of() }} as checked_at_utc
from {{ ref('metric_registry') }} as r
left join stale as s on s.metric_id = r.metric_id
left join failed_agg as f on f.metric_id = r.metric_id
