-- Order count, gross-to-net and ad spend must close to zero unexplained in every scenario.
select check_name, unexplained
from {{ ref('rec_summary') }}
where check_name in ('order_count', 'gross_to_net', 'ad_spend') and unexplained <> 0
