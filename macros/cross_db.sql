{# Everything engine-specific lives here. DuckDB runs in CI; the default__ branches are the BigQuery forms. #}

{% macro utc_timestamp(col) %}{{ return(adapter.dispatch('utc_timestamp')(col)) }}{% endmacro %}
{% macro default__utc_timestamp(col) %}cast({{ col }} as timestamp){% endmacro %}
{% macro duckdb__utc_timestamp(col) %}cast(cast({{ col }} as timestamptz) as timestamp){% endmacro %}

{# Business date in Europe/London from a UTC instant: handles midnight and both clock changes. #}
{% macro london_date(ts) %}{{ return(adapter.dispatch('london_date')(ts)) }}{% endmacro %}
{% macro default__london_date(ts) %}date({{ ts }}, 'Europe/London'){% endmacro %}
{% macro duckdb__london_date(ts) %}cast(timezone('Europe/London', cast({{ ts }} as timestamptz)) as date){% endmacro %}

{# Timestamp and date arithmetic that keeps the type: dbt.dateadd returns DATETIME on BigQuery,
   which cannot be compared with a TIMESTAMP. #}
{% macro ts_add_days(ts, n) %}{{ return(adapter.dispatch('ts_add_days')(ts, n)) }}{% endmacro %}
{% macro default__ts_add_days(ts, n) %}timestamp_add({{ ts }}, interval {{ n }} day){% endmacro %}
{% macro duckdb__ts_add_days(ts, n) %}({{ ts }} + to_days(cast({{ n }} as integer))){% endmacro %}

{% macro ts_add_hours(ts, n) %}{{ return(adapter.dispatch('ts_add_hours')(ts, n)) }}{% endmacro %}
{% macro default__ts_add_hours(ts, n) %}timestamp_add({{ ts }}, interval {{ n }} hour){% endmacro %}
{% macro duckdb__ts_add_hours(ts, n) %}({{ ts }} + to_hours(cast({{ n }} as integer))){% endmacro %}

{% macro date_add_days(d, n) %}{{ return(adapter.dispatch('date_add_days')(d, n)) }}{% endmacro %}
{% macro default__date_add_days(d, n) %}date_add({{ d }}, interval {{ n }} day){% endmacro %}
{% macro duckdb__date_add_days(d, n) %}cast({{ d }} + to_days(cast({{ n }} as integer)) as date){% endmacro %}

{% macro type_double() %}{{ return(adapter.dispatch('type_double')()) }}{% endmacro %}
{% macro default__type_double() %}float64{% endmacro %}
{% macro duckdb__type_double() %}double{% endmacro %}

{% macro month_start(d) %}cast({{ dbt.date_trunc('month', d) }} as date){% endmacro %}

{% macro day_series(start_date, end_date) %}{{ return(adapter.dispatch('day_series')(start_date, end_date)) }}{% endmacro %}
{% macro default__day_series(start_date, end_date) %}
select day from unnest(generate_date_array(cast({{ start_date }} as date), cast({{ end_date }} as date))) as day
{% endmacro %}
{% macro duckdb__day_series(start_date, end_date) %}
select cast(d as date) as day
from (select unnest(generate_series(cast({{ start_date }} as date), cast({{ end_date }} as date), interval 1 day)) as d) as gs
{% endmacro %}

{% macro regex_replace_all(s, pattern, repl) %}{{ return(adapter.dispatch('regex_replace_all')(s, pattern, repl)) }}{% endmacro %}
{% macro default__regex_replace_all(s, pattern, repl) %}regexp_replace({{ s }}, {{ pattern }}, {{ repl }}){% endmacro %}
{% macro duckdb__regex_replace_all(s, pattern, repl) %}regexp_replace({{ s }}, {{ pattern }}, {{ repl }}, 'g'){% endmacro %}

{% macro to_pence(col) %}cast(round(cast({{ col }} as {{ dbt.type_numeric() }}) * 100) as {{ dbt.type_bigint() }}){% endmacro %}
{% macro to_int(col) %}cast({{ col }} as {{ dbt.type_bigint() }}){% endmacro %}

{% macro as_of() %}cast('{{ var("as_of") }}' as {{ dbt.type_timestamp() }}){% endmacro %}
{% macro as_of_date() %}{{ london_date(as_of()) }}{% endmacro %}

{% macro sql_str(s) %}'{{ (s | string) | replace("'", "''") }}'{% endmacro %}

{# Email normalisation: three separate operations, each a signal for review, never a merge rule. #}
{% macro email_lower(e) %}lower(trim({{ e }})){% endmacro %}
{% macro email_without_plus(e) %}regexp_replace({{ e }}, '[+][^@]*@', '@'){% endmacro %}
{% macro email_without_local_dots(e) %}replace(regexp_extract({{ e }}, '^[^@]*'), '.', '') || regexp_extract({{ e }}, '@.*$'){% endmacro %}

{% test unique_combination(model, columns) %}
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}
