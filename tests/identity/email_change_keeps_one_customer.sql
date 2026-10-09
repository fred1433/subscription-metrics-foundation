-- Fixture F1: the platform account changed its email; the Shopify customer kept the old one.
-- Expect one customer, linked by the cross-reference, with its whole order history on both sides of the change.
with c as (
    select * from {{ ref('dim_customers') }} where platform_account_id = 'ACC-F1'
),
o as (
    select f.*
    from {{ ref('fct_orders') }} as f
    inner join c on c.customer_key = f.customer_key
),
landed as (
    select count(*) as n from {{ ref('stg_shopify__orders') }} where shopify_customer_id = 70011
)
select 'expected exactly one customer for ACC-F1' as failure from (select count(*) as n from c) as x where n <> 1
union all
select 'customer not linked by the cross-reference' from c where link_rule <> 'xref_platform_to_shopify'
union all
select 'orders of the Shopify customer not all attached' from landed where n <> (select count(*) from o) or n < 4
union all
select 'history does not span the email change'
from (select min(created_at_utc) as first_at, max(created_at_utc) as last_at from o) as h
where not (h.first_at < cast('2026-02-03 11:00:00' as timestamp) and h.last_at > cast('2026-02-03 11:00:00' as timestamp))
