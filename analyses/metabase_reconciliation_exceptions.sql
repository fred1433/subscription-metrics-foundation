-- Metabase-ready: every reconciliation item, exceptions first. Amounts are warehouse minus source, in pence.
-- (Not run inside Metabase in this repository.)
select
    check_name,
    classification,
    category,
    item_key,
    amount_pence / 100.0 as amount_gbp,
    note
from {{ ref('rec_exceptions') }}
order by case when classification = 'exception' then 0 else 1 end, check_name, item_key
