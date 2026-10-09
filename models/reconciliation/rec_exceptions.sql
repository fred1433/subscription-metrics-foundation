select * from {{ ref('rec_order_count') }}
union all select * from {{ ref('rec_completeness') }}
union all select * from {{ ref('rec_order_total_to_cash') }}
union all select * from {{ ref('rec_ad_spend') }}
union all select * from {{ ref('rec_trial_eligibility') }}
union all
select
    'lines_to_order_total' as check_name,
    item_key,
    'line_total_mismatch' as category,
    'exception' as classification,
    residual_pence as amount_pence,
    0 as order_count_delta,
    'lines minus discounts differ from the order total' as note
from {{ ref('rec_order_bridge') }}
