-- deduplicated on the FULL source key; platform + day alone would merge distinct campaigns
with ranked as (
    select
        *,
        row_number() over (
            partition by platform, account_id, campaign_id, date
            order by _synced_at desc
        ) as rn
    from {{ source('ads', 'daily_spend') }}
    where {{ utc_timestamp('_synced_at') }} <= {{ as_of() }}
)
select
    platform,
    account_id,
    campaign_id,
    campaign_name,
    cast(date as date) as spend_date_london,   -- accounts report in Europe/London, see README
    {{ to_pence('spend') }} as spend_pence,
    {{ to_int('impressions') }} as impressions,
    {{ to_int('clicks') }} as clicks,
    {{ utc_timestamp('_synced_at') }} as synced_at_utc
from ranked
where rn = 1
