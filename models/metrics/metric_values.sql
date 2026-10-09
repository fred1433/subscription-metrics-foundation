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

month_ends as (
    select {{ month_start('day') }} as period, max(day) as month_end
    from ({{ day_series("'2025-04-01'", as_of_date()) }}) as d
    group by {{ month_start('day') }}
),

active as (
    {%- for slice_name, slice_expr in [('total', "'all'"), ('cats_on_plan', 'sub.cats_on_plan_band')] %}
    select
        'active_subscriptions' as metric_id,
        m.period,
        '{{ slice_name }}' as slice_name,
        {{ slice_expr }} as slice_value,
        count(*) as numerator,
        cast(null as {{ dbt.type_bigint() }}) as denominator,
        count(*) as group_size,
        cast(null as {{ dbt.type_bigint() }}) as pending_count
    from {{ ref('fct_subscription_state_daily') }} as s
    inner join month_ends as m on m.month_end = s.state_date
    inner join {{ ref('stg_platform__subscriptions') }} as sub on sub.subscription_id = s.subscription_id
    where s.subscription_state in ('active', 'payment_retry')
    group by m.period, {{ slice_expr }}
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
        when r.aggregation = 'ratio_of_sums' then 1.0 * u.numerator / nullif(u.denominator, 0)
        else 1.0 * u.numerator
    end as value,
    u.group_size,
    u.pending_count,
    r.version as definition_version,
    {{ as_of() }} as cutoff_utc
from unioned as u
inner join {{ ref('metric_registry') }} as r on r.metric_id = u.metric_id
