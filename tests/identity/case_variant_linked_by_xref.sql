-- Fixture F5: emails differ only by case; the link exists because of the cross-reference, and the
-- review queue stays empty for this customer.
select 'case variant not linked' as failure
from (select count(*) as n from {{ ref('dim_customers') }} where platform_account_id = 'ACC-F5' and shopify_customer_id = 70051) as x
where n <> 1
union all
select 'linked customer appears in the review queue'
from {{ ref('int_identity_candidates') }}
where customer_key_a = 'shopify:70051' or customer_key_b = 'shopify:70051'
