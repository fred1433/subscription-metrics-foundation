-- Fixture F4 (defective scenario): 'Ella.Hart+untamed@...' and 'ellahart@...' only meet after
-- lower-casing, dropping the +alias and the dots. Expect two customers and one unresolved candidate.
-- Generic part (both scenarios): no customer joins two Shopify customers, so no merge came from an email.
select 'customers merged without a cross-reference' as failure
from {{ ref('int_identity_links') }}
where source_system = 'shopify'
group by customer_key
having count(*) > 1
union all
select 'alias pair merged'
from {{ ref('dim_customers') }}
where shopify_customer_id in (70041, 70042)
group by customer_key
having count(*) > 1
union all
select 'alias pair missing from the review queue'
from (select count(*) as n from {{ ref('dim_customers') }} where shopify_customer_id in (70041, 70042)) as c
where c.n = 2 and not exists (
    select 1 from {{ ref('int_identity_candidates') }}
    where customer_key_a = 'shopify:70041' and customer_key_b = 'shopify:70042' and resolution = 'unresolved'
)
