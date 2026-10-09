{{ config(severity='warn') }}
select * from {{ ref('rec_ad_spend') }}
