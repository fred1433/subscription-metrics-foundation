-- The 73p card check is a payment transaction that is reversed; it never reaches revenue.
select 'card checks do not net to zero' as failure
from (
    select sum(amount_pence) as net
    from {{ ref('stg_platform__payment_transactions') }}
    where kind in ('card_check', 'card_check_reversal')
) as x
where net <> 0
union all
select 'journey card check not listed as reversed'
from (select count(*) as n from {{ ref('rec_order_total_to_cash') }}
      where item_key = 'ACC-J:card_check:2026-06-07' and category = 'card_check_reversed') as y
where n <> 1
