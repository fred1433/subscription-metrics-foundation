{#
  Identity rule (confirmed links only):
    a platform account and a Shopify customer are the same customer when the account carries an explicit
    cross-reference to that Shopify customer id (assumed field accounts.shopify_customer_id).
  Nothing else merges customers: not an email, not a normalised email, not an address.
  Every link keeps its rule and its evidence.
#}
with accounts as (
    select * from {{ ref('stg_platform__accounts') }}
),

shopify as (
    select * from {{ ref('stg_shopify__customers') }}
),

referenced as (
    select distinct shopify_customer_id from accounts where shopify_customer_id is not null
)

select
    'platform' as source_system,
    a.account_id as source_id,
    case
        when a.shopify_customer_id is not null
            then 'shopify:' || cast(a.shopify_customer_id as {{ dbt.type_string() }})
        else 'platform:' || a.account_id
    end as customer_key,
    case when a.shopify_customer_id is not null then 'xref_platform_to_shopify' else 'unlinked_platform_account' end
        as link_rule,
    case
        when a.shopify_customer_id is not null
            then 'accounts.shopify_customer_id = ' || cast(a.shopify_customer_id as {{ dbt.type_string() }})
        else 'no cross-reference on the account'
    end as link_evidence
from accounts as a

union all

select
    'shopify' as source_system,
    cast(s.shopify_customer_id as {{ dbt.type_string() }}) as source_id,
    'shopify:' || cast(s.shopify_customer_id as {{ dbt.type_string() }}) as customer_key,
    case when r.shopify_customer_id is not null then 'xref_platform_to_shopify' else 'shopify_only' end as link_rule,
    case
        when r.shopify_customer_id is not null then 'referenced by a platform account'
        else 'no platform account references this customer'
    end as link_evidence
from shopify as s
left join referenced as r on r.shopify_customer_id = s.shopify_customer_id
