-- Two zero-value orders: the blank order (card update) leaves the commercial count, the free replacement
-- stays (it shipped and has a cost). Only the motif decides.
select 'blank order counted as commercial' as failure
from {{ ref('fct_orders') }} where platform_order_id = 'PO-J-BLANK' and is_commercial
union all
select 'free replacement dropped'
from {{ ref('fct_orders') }} where platform_order_id = 'PO-F6-R1' and not (is_commercial and original_total_pence = 0)
union all
select 'non-commercial order without the blank motif'
from {{ ref('fct_orders') }} where not is_commercial and order_motif <> 'payment_method_update'
