-- Every slice of a month adds up to the month total, on numerator and denominator separately.
with t as (
    select metric_id, period, numerator, denominator
    from {{ ref('metric_values') }} where slice_name = 'total'
),
s as (
    select metric_id, period, slice_name, sum(numerator) as numerator, sum(coalesce(denominator, 0)) as denominator
    from {{ ref('metric_values') }} where slice_name <> 'total'
    group by metric_id, period, slice_name
)
select s.metric_id, s.period, s.slice_name
from s
inner join t on t.metric_id = s.metric_id and t.period = s.period
where s.numerator <> t.numerator or s.denominator <> coalesce(t.denominator, 0)
