-- Cleaned, business-hours-only snapshot grain.
-- Everything downstream reads from here, never from raw.

with source as (

    select * from {{ source('raw', 'wait_time_snapshots') }}

),

cleaned as (

    select
        captured_at,
        cast(captured_at as date)                    as captured_date,
        extract(hour from captured_at)               as captured_hour,
        dayname(captured_at)                         as day_name,
        extract(dow from captured_at) in (0, 6)      as is_weekend,

        park_id,
        park_name,
        land_name,
        ride_id,
        ride_name,

        is_open,
        data_source,

        -- A closed ride has no meaningful posted wait. Null it rather than
        -- carrying a zero that would drag down average-wait calculations.
        case when is_open then coalesce(wait_time_minutes, 0) end
                                                     as wait_time_minutes

    from source
    where extract(hour from captured_at) >= {{ var('park_open_hour') }}
      and extract(hour from captured_at) <  {{ var('park_close_hour') }}

)

select * from cleaned
