select * from {{ ref('rec_order_count') }}
union all select * from {{ ref('rec_completeness') }}
union all select * from {{ ref('rec_gross_to_net') }}
union all select * from {{ ref('rec_ad_spend') }}
union all select * from {{ ref('rec_trial_eligibility') }}
