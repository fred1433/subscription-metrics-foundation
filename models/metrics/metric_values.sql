{#
  Every implemented metric, pre-aggregated per month and per allowed slice, with numerator and denominator.
  The MCP server only selects from here; combining months is sum(numerator) / sum(denominator).
#}
with trials as (
    select * from {{ ref('fct_trials') }}
),

conversion as (
    {%- for slice_name, slice_expr in [('total', "'all'"), ('cats_on_plan', 'cats_on_plan_band'),
                                        ('referred', "case when is_referred then 'yes' else 'no' end")] %}
    select
        'trial_conversion_rate' as metric_id,
        cohort_month as period,
        '{{ slice_name }}' as slice_name,
        {{ slice_expr }} as slice_value,
        sum(case when is_window_complete and converted_in_window then 1 else 0 end) as numerator,
        sum(case when is_window_complete then 1 else 0 end) as denominator,
        sum(case when is_window_complete then 1 else 0 end) as group_size,
        sum(case when is_window_complete then 0 else 1 end) as pending_count
    from trials
    group by cohort_month, {{ slice_expr }}
    {% if not loop.last %}union all{% endif %}
    {%- endfor %}
),

revenue as (
    {%- for slice_name, slice_expr in [('total', "'all'"), ('order_kind', 'order_kind')] %}
    select
        'net_revenue' as metric_id,
        movement_month as period,
        '{{ slice_name }}' as slice_name,
        {{ slice_expr }} as slice_value,
        sum(amount_pence) as numerator,
        cast(null as {{ dbt.type_bigint() }}) as denominator,
        count(distinct order_key) as group_size,
        cast(null as {{ dbt.type_bigint() }}) as pending_count
    from {{ ref('fct_revenue_movements') }}
    group by movement_month, {{ slice_expr }}
    {% if not loop.last %}union all{% endif %}
    {%- endfor %}
),

active as (
    {%- for slice_name, slice_expr in [('total', "'all'"), ('cats_on_plan', 'cats_on_plan_band')] %}
    select
        'active_subscriptions' as metric_id,
        period,
        '{{ slice_name }}' as slice_name,
        {{ slice_expr }} as slice_value,
        count(*) as numerator,
        cast(null as {{ dbt.type_bigint() }}) as denominator,
        count(*) as group_size,
        cast(null as {{ dbt.type_bigint() }}) as pending_count
    from {{ ref('fct_active_subscriptions_month_end') }}
    group by period, {{ slice_expr }}
    {% if not loop.last %}union all{% endif %}
    {%- endfor %}
),

spend as (
    select {{ month_start('spend_date_london') }} as period, sum(spend_pence) as spend_pence
    from {{ ref('fct_ad_spend_daily') }}
    group by {{ month_start('spend_date_london') }}
),

trial_counts as (
    select cohort_month as period, count(*) as trials from trials group by cohort_month
),

ad_spend_per_trial as (
    select
        'ad_spend_per_trial' as metric_id,
        s.period,
        'total' as slice_name,
        'all' as slice_value,
        s.spend_pence as numerator,
        coalesce(t.trials, 0) as denominator,
        coalesce(t.trials, 0) as group_size,
        cast(null as {{ dbt.type_bigint() }}) as pending_count
    from spend as s
    left join trial_counts as t on t.period = s.period
),

unioned as (
    select * from conversion
    union all select * from revenue
    union all select * from active
    union all select * from ad_spend_per_trial
)

select
    u.metric_id,
    u.period,
    u.slice_name,
    u.slice_value,
    u.numerator,
    u.denominator,
    case
        when r.aggregation = 'ratio_of_sums' then cast(u.numerator as {{ type_double() }}) / nullif(u.denominator, 0)
        else cast(u.numerator as {{ type_double() }})
    end as value,
    u.group_size,
    u.pending_count,
    r.version as definition_version,
    {{ as_of() }} as cutoff_utc
from unioned as u
inner join {{ ref('metric_registry') }} as r on r.metric_id = u.metric_id
