-- Fixture F8: UTC instants around midnight and both clock changes, mapped to the Europe/London business date.
with expected as (
    select cast('2025-07-01 23:15:00' as timestamp) as created_at_utc, cast('2025-07-02' as date) as london_date
    union all select cast('2025-10-25 23:30:00' as timestamp), cast('2025-10-26' as date)   -- still BST
    union all select cast('2025-10-26 23:30:00' as timestamp), cast('2025-10-26' as date)   -- GMT again
    union all select cast('2026-03-28 23:30:00' as timestamp), cast('2026-03-28' as date)   -- GMT
    union all select cast('2026-03-29 23:30:00' as timestamp), cast('2026-03-30' as date)   -- BST began
)
select e.created_at_utc, e.london_date, o.order_date_london
from expected as e
left join {{ ref('fct_orders') }} as o
    on o.created_at_utc = e.created_at_utc and o.customer_key = 'shopify:70081'
where o.order_date_london is null or o.order_date_london <> e.london_date
