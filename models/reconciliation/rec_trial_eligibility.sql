select
    'trial_eligibility' as check_name,
    t.trial_order_id as item_key,
    t.eligibility_exception as category,
    'exception' as classification,
    o.original_total_pence as amount_pence,
    0 as order_count_delta,
    'kept as a real sale; flagged for the one-trial-per-household rule' as note
from {{ ref('fct_trials') }} as t
left join {{ ref('fct_orders') }} as o on o.platform_order_id = t.trial_order_id
where t.eligibility_exception is not null
