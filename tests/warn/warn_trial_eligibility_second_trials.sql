{{ config(severity='warn') }}
select * from {{ ref('rec_trial_eligibility') }}
