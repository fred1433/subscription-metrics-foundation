-- Fixture F6: one box, 3 lines (12 sleeves at 6.40), refunds: one sleeve (6.40) paid, goodwill 3.00 paid,
-- goodwill 2.00 recorded but pending. Original 76.80, current 65.40, paid back 9.40, net 67.40.
-- The joins to lines and refunds must not multiply anything.
with o as (
    select * from {{ ref('fct_orders') }} where platform_order_id = 'PO-F6-B1'
),
m as (
    select sum(amount_pence) as net from {{ ref('fct_revenue_movements') }} where platform_order_id = 'PO-F6-B1'
)
select 'expected one order row' as failure from (select count(*) as n from o) as x where n <> 1
union all
select 'amounts differ from the hand computation'
from o
where not (line_count = 3 and gross_pence = 7680 and original_total_pence = 7680 and current_total_pence = 6540
           and refunds_recorded_pence = 1140 and refunds_paid_pence = 940 and net_revenue_pence = 6740)
union all
select 'revenue movements do not add up to 67.40' from m where net <> 6740
