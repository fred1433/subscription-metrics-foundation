{#
  Review queue, not a merge. Three normalisations, each documented and kept apart:
    1. email_lower                 lower-case and trim
    2. email_without_plus          drops a "+tag" before the @ (alias)
    3. email_canonical             also drops dots in the local part (a Gmail behaviour, not universal)
  Two different customers whose emails meet on any of them become a candidate pair, status unresolved.
#}
with links as (
    select * from {{ ref('int_identity_links') }}
),

emails as (
    select l.customer_key, h.email
    from {{ ref('stg_platform__account_email_history') }} as h
    inner join links as l on l.source_system = 'platform' and l.source_id = h.account_id
    union all
    select l.customer_key, a.email
    from {{ ref('stg_platform__accounts') }} as a
    inner join links as l on l.source_system = 'platform' and l.source_id = a.account_id
    union all
    select 'shopify:' || cast(shopify_customer_id as {{ dbt.type_string() }}), email
    from {{ ref('stg_shopify__customers') }}
),

normalised as (
    select distinct
        customer_key,
        email,
        {{ email_lower('email') }} as email_lower,
        {{ email_without_plus(email_lower('email')) }} as email_without_plus,
        {{ email_without_local_dots(email_without_plus(email_lower('email'))) }} as email_canonical
    from emails
),

pairs as (
    select
        a.customer_key as customer_key_a,
        b.customer_key as customer_key_b,
        case
            when a.email_lower = b.email_lower then 'case_only'
            when a.email_without_plus = b.email_without_plus then 'plus_alias'
            else 'plus_alias_and_dots'
        end as matched_on
    from normalised as a
    inner join normalised as b
        on a.email_canonical = b.email_canonical
        and a.customer_key < b.customer_key
)

select distinct
    customer_key_a,
    customer_key_b,
    matched_on,
    'unresolved' as resolution,
    'normalised email only: needs a cross-reference or a human decision' as note
from pairs
