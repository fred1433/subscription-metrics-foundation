-- Order count, both halves of gross-to-net and ad spend must close to zero unexplained in every scenario.
select check_name, unexplained
from {{ ref('rec_summary') }}
where check_name in ('order_count', 'lines_to_order_total', 'order_total_to_cash', 'ad_spend') and (unexplained <> 0 or unexplained_gross <> 0)
