-- Park-level daily rollup. This is the executive scorecard grain.

with ride_daily as (

    select * from {{ ref('agg_ride_daily') }}

)

select
    captured_date,
    park_name,
    max(day_name)                                   as day_name,
    max(is_weekend)                                 as is_weekend,

    count(distinct ride_id)                         as rides_tracked,

    -- Weighted by snapshots so a ride with partial coverage does not skew
    -- the park number as much as a fully observed one.
    round(
        100.0 * sum(snapshots - closed_snapshots) / nullif(sum(snapshots), 0), 2
    )                                               as park_uptime_pct,

    sum(downtime_events)                            as total_downtime_events,
    sum(downtime_minutes)                           as total_downtime_minutes,
    round(sum(downtime_minutes) / 60.0, 1)          as total_downtime_hours,

    round(avg(avg_wait_when_open), 1)               as avg_wait_across_rides,
    max(peak_wait)                                  as peak_wait_in_park,

    count(distinct case when downtime_events > 0 then ride_id end)
                                                    as rides_with_downtime

from ride_daily
group by 1, 2
