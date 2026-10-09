{{ config(severity='warn') }}
select * from {{ ref('int_identity_candidates') }} where resolution = 'unresolved'
