-- One row per ride, with a reliability profile computed over the full history.
-- This is the "which assets are chronically unreliable" table.

with snapshots as (

    select * from {{ ref('stg_snapshots') }}

),

ride_base as (

    select
        ride_id,
        max(ride_name)                                       as ride_name,
        max(park_name)                                       as park_name,
        max(land_name)                                       as land_name,

        count(*)                                             as total_snapshots,
        sum(case when is_open then 1 else 0 end)             as open_snapshots,

        round(avg(wait_time_minutes), 1)                     as avg_wait_when_open,
        max(wait_time_minutes)                               as peak_wait_observed,

        min(captured_at)                                     as first_observed_at,
        max(captured_at)                                     as last_observed_at

    from snapshots
    group by 1

),

downtime as (

    select
        ride_id,
        count(*)                    as downtime_event_count,
        sum(downtime_minutes)       as total_downtime_minutes,
        round(avg(downtime_minutes), 1) as avg_downtime_minutes,
        max(downtime_minutes)       as longest_downtime_minutes
    from {{ ref('fct_downtime_events') }}
    group by 1

),

final as (

    select
        b.ride_id,
        b.ride_name,
        b.park_name,
        b.land_name,

        round(100.0 * b.open_snapshots / nullif(b.total_snapshots, 0), 2) as uptime_pct,

        coalesce(d.downtime_event_count, 0)      as downtime_event_count,
        coalesce(d.total_downtime_minutes, 0)    as total_downtime_minutes,
        coalesce(d.avg_downtime_minutes, 0)      as avg_downtime_minutes,
        coalesce(d.longest_downtime_minutes, 0)  as longest_downtime_minutes,

        b.avg_wait_when_open,
        b.peak_wait_observed,

        -- Demand-weighted impact: an hour of downtime on a ride with a 90 minute
        -- standby line hurts far more guests than an hour on a walk-on. This is
        -- the metric that should drive maintenance prioritisation, not raw
        -- downtime minutes.
        round(
            coalesce(d.total_downtime_minutes, 0) * coalesce(b.avg_wait_when_open, 0) / 60.0,
            0
        ) as guest_impact_score,

        b.total_snapshots,
        b.first_observed_at,
        b.last_observed_at

    from ride_base b
    left join downtime d using (ride_id)

)

select * from final
