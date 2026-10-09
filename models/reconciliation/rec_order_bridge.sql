-- One row per order whose lines minus discounts do not equal its order total. Residuals are kept signed
-- AND absolute: +1.00 on one order and -1.00 on another are two exceptions, not zero.
select
    coalesce(platform_order_id, order_key) as item_key,
    order_key,
    gross_pence,
    discount_pence,
    original_total_pence,
    gross_pence - discount_pence - original_total_pence as residual_pence
from {{ ref('fct_orders') }}
where gross_pence - discount_pence - original_total_pence <> 0
