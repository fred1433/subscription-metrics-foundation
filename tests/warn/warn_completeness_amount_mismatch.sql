{{ config(severity='warn') }}
-- Expected to WARN in the defective scenario and stay silent in the clean one (see harness/).
select * from {{ ref('rec_completeness') }} where category = 'amount_mismatch'
