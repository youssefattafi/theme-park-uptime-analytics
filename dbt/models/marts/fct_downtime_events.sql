-- The centerpiece model.
--
-- Raw data tells us a ride was closed at 2:15pm, and closed at 2:30pm, and
-- closed at 2:45pm. That is three rows, but it is ONE breakdown. Counting rows
-- would triple-count outages and make a single long failure look identical to
-- three quick ones - which are very different operational problems.
--
-- This model uses a gap-and-islands pattern to collapse consecutive closed
-- snapshots into discrete downtime events, so "how many times did it break"
-- and "how long was it down" become separate, answerable questions.

with snapshots as (

    select
        ride_id,
        ride_name,
        park_name,
        land_name,
        captured_at,
        captured_date,
        is_open
    from {{ ref('stg_snapshots') }}

),

-- Mark the row where a ride's status flips from its previous observation.
status_changes as (

    select
        *,
        case
            when lag(is_open) over (partition by ride_id order by captured_at) = is_open
                then 0
            else 1
        end as is_status_change
    from snapshots

),

-- Running sum of flips gives every uninterrupted run of identical status a
-- shared group id - the "island".
islands as (

    select
        *,
        sum(is_status_change) over (
            partition by ride_id
            order by captured_at
            rows between unbounded preceding and current row
        ) as status_group
    from status_changes

),

-- Keep only the closed islands and collapse each into a single event row.
closed_runs as (

    select
        ride_id,
        ride_name,
        park_name,
        land_name,
        status_group,
        min(captured_at)  as downtime_started_at,
        max(captured_at)  as last_observed_closed_at,
        min(captured_date) as downtime_date,
        count(*)          as closed_snapshot_count
    from islands
    where is_open = false
    group by 1, 2, 3, 4, 5

),

final as (

    select
        md5(cast(ride_id as varchar) || '|' || cast(downtime_started_at as varchar))
                                                            as downtime_event_id,
        ride_id,
        ride_name,
        park_name,
        land_name,
        downtime_date,
        downtime_started_at,
        last_observed_closed_at,
        closed_snapshot_count,

        -- Each snapshot represents one interval of observed downtime.
        closed_snapshot_count * {{ var('snapshot_interval_minutes') }}
                                                            as downtime_minutes,

        round(
            closed_snapshot_count * {{ var('snapshot_interval_minutes') }} / 60.0, 2
        )                                                   as downtime_hours,

        extract(hour from downtime_started_at)              as started_hour,
        dayname(downtime_started_at)                        as started_day_name,

        -- Operational severity banding, so leadership can filter to what matters.
        case
            when closed_snapshot_count * {{ var('snapshot_interval_minutes') }} >= 120
                then 'Major (2h+)'
            when closed_snapshot_count * {{ var('snapshot_interval_minutes') }} >= 60
                then 'Significant (1-2h)'
            when closed_snapshot_count * {{ var('snapshot_interval_minutes') }} >= 30
                then 'Moderate (30-60m)'
            else 'Brief (<30m)'
        end                                                 as severity_band

    from closed_runs
    where closed_snapshot_count * {{ var('snapshot_interval_minutes') }}
          >= {{ var('min_downtime_minutes') }}

)

select * from final
