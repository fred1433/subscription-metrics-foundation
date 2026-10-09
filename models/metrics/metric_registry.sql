-- the registry macro, materialised: the only definition table the MCP server reads
{%- set reg = metric_registry() %}
{%- for m in reg['metrics'] %}
select
    {{ sql_str(m['id']) }} as metric_id,
    cast({{ m['version'] }} as {{ dbt.type_int() }}) as version,
    {{ sql_str(m['status']) }} as status,
    {{ sql_str(m['label']) }} as label,
    {{ sql_str(m['definition']) }} as definition,
    {{ sql_str(m.get('grain', '')) }} as grain,
    {{ sql_str(m.get('numerator', '')) }} as numerator,
    {{ sql_str(m.get('denominator', '')) }} as denominator,
    {{ sql_str(m.get('unit', '')) }} as unit,
    {{ sql_str(m.get('aggregation', '')) }} as aggregation,
    {{ sql_str(tojson(m.get('allowed_slices', []))) }} as allowed_slices_json,
    cast({{ m.get('min_group_size', 0) }} as {{ dbt.type_int() }}) as min_group_size,
    cast({{ m.get('window_days') or 'null' }} as {{ dbt.type_int() }}) as window_days,
    {{ sql_str(m.get('maturity_policy', '')) }} as maturity_policy,
    {{ sql_str(tojson(m.get('source_dependencies', []))) }} as source_dependencies_json,
    {{ sql_str(tojson(m.get('reconciliation_checks', []))) }} as reconciliation_checks_json,
    {{ sql_str(m.get('required_quality_status', '')) }} as required_quality_status,
    {{ sql_str(tojson(m.get('missing_inputs', []))) }} as missing_inputs_json
{% if not loop.last %}union all{% endif %}
{%- endfor %}
