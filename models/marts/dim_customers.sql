-- one row per customer (not per household, not per cat); see int_identity_links for the rule
with links as (
    select * from {{ ref('int_identity_links') }}
),

accounts as (
    select * from {{ ref('stg_platform__accounts') }}
),

shopify as (
    select * from {{ ref('stg_shopify__customers') }}
),

grouped as (
    select
        customer_key,
        max(case when source_system = 'platform' then source_id end) as platform_account_id,
        max(case when source_system = 'shopify' then source_id end) as shopify_customer_id,
        max(link_rule) as link_rule,
        max(case when source_system = 'platform' then link_evidence end) as link_evidence,
        count(*) as source_records
    from links
    group by customer_key
)

select
    g.customer_key,
    g.platform_account_id,
    {{ to_int('g.shopify_customer_id') }} as shopify_customer_id,
    g.link_rule,
    coalesce(g.link_evidence, 'shopify record only') as link_evidence,
    g.source_records,
    coalesce(a.email, s.email) as email_current,
    case when a.created_at_utc is null or s.created_at_utc < a.created_at_utc then s.created_at_utc else a.created_at_utc end
        as first_seen_at_utc,
    g.platform_account_id is not null as has_subscription_account
from grouped as g
left join accounts as a on a.account_id = g.platform_account_id
left join shopify as s on cast(s.shopify_customer_id as {{ dbt.type_string() }}) = g.shopify_customer_id
