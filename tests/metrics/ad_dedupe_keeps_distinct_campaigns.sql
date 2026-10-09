-- Deduplication is on the full source key: several campaigns of one platform on one day all survive.
select 'deduplicated on platform and day' as failure
from (
    select count(*) as rows_kept,
           count(distinct platform || '|' || cast(spend_date_london as {{ dbt.type_string() }})) as platform_days
    from {{ ref('fct_ad_spend_daily') }}
) as x
where rows_kept <= platform_days
union all
select 'a distinct source key was lost'
from (select count(*) as n from (select distinct platform, account_id, campaign_id, date from {{ source('ads', 'daily_spend') }}) as d) as a,
     (select count(*) as n from {{ ref('fct_ad_spend_daily') }}) as b
where a.n <> b.n
