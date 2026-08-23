-- Daily KPI grain per ride. Feeds trend lines and day-over-day comparisons.

with snapshots as (

    select * from {{ ref('stg_snapshots') }}

),

daily as (

    select
        captured_date,
        ride_id,
        max(ride_name)                              as ride_name,
        max(park_name)                              as park_name,
        max(land_name)                              as land_name,
        max(is_weekend)                             as is_weekend,
        max(day_name)                               as day_name,

        count(*)                                    as snapshots,
        sum(case when is_open then 1 else 0 end)    as open_snapshots,
        sum(case when is_open then 0 else 1 end)    as closed_snapshots,

        round(avg(wait_time_minutes), 1)            as avg_wait_when_open,
        max(wait_time_minutes)                      as peak_wait

    from snapshots
    group by 1, 2

),

events as (

    select
        downtime_date as captured_date,
        ride_id,
        count(*)              as downtime_events,
        sum(downtime_minutes) as downtime_minutes
    from {{ ref('fct_downtime_events') }}
    group by 1, 2

)

select
    d.captured_date,
    d.ride_id,
    d.ride_name,
    d.park_name,
    d.land_name,
    d.day_name,
    d.is_weekend,

    round(100.0 * d.open_snapshots / nullif(d.snapshots, 0), 2) as uptime_pct,

    coalesce(e.downtime_events, 0)   as downtime_events,
    coalesce(e.downtime_minutes, 0)  as downtime_minutes,

    d.avg_wait_when_open,
    d.peak_wait,
    d.snapshots,
    d.closed_snapshots

from daily d
left join events e
    on d.captured_date = e.captured_date
   and d.ride_id = e.ride_id
