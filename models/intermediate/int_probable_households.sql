{#
  Households are NOT customers. A household groups customers who share a normalised delivery address.
    possible  : same address only (flatmates, family members)
    probable  : same address and same payment fingerprint (assumed field)
  The public terms use email, payment method and delivery address to decide referral eligibility;
  here they only feed an eligibility review, never the customer identity.
#}
with acc as (
    select
        l.customer_key,
        a.account_id,
        a.payment_fingerprint,
        {{ regex_replace_all("lower(a.delivery_address_line1 || a.delivery_postcode)", "'[^a-z0-9]'", "''") }}
            as address_key
    from {{ ref('stg_platform__accounts') }} as a
    inner join {{ ref('int_identity_links') }} as l
        on l.source_system = 'platform' and l.source_id = a.account_id
),

shared_address as (
    select address_key, count(distinct customer_key) as customers_at_address
    from acc
    group by address_key
    having count(distinct customer_key) > 1
),

shared_card as (
    select address_key, payment_fingerprint, count(distinct customer_key) as customers_with_card
    from acc
    group by address_key, payment_fingerprint
),

joined as (
    select
        {{ dbt.hash('acc.address_key') }} as household_id,
        acc.customer_key,
        acc.account_id,
        s.customers_at_address,
        coalesce(c.customers_with_card, 1) > 1 as shares_payment_fingerprint,
        acc.address_key
    from acc
    inner join shared_address as s on s.address_key = acc.address_key
    left join shared_card as c
        on c.address_key = acc.address_key and c.payment_fingerprint = acc.payment_fingerprint
)

select
    household_id,
    customer_key,
    account_id,
    customers_at_address,
    shares_payment_fingerprint,
    case
        when max(case when shares_payment_fingerprint then 1 else 0 end) over (partition by household_id) = 1
            then 'probable'
        else 'possible'
    end as confidence,
    case
        when shares_payment_fingerprint then 'same normalised delivery address and same payment fingerprint'
        else 'same normalised delivery address'
    end as evidence
from joined
