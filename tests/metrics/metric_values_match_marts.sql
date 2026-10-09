-- What the MCP server can serve equals the marts: net revenue against revenue movements,
-- conversion against trials.
select 'net revenue differs from movements' as failure
from (select sum(numerator) as v from {{ ref('metric_values') }} where metric_id = 'net_revenue' and slice_name = 'total') as a,
     (select sum(amount_pence) as v from {{ ref('fct_revenue_movements') }}) as b
where a.v <> b.v
union all
select 'conversions differ from trials'
from (select sum(numerator) as v from {{ ref('metric_values') }} where metric_id = 'trial_conversion_rate' and slice_name = 'total') as a,
     (select count(*) as v from {{ ref('fct_trials') }} where is_window_complete and converted_in_window) as b
where a.v <> b.v
