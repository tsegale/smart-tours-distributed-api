-- Development and benchmark data for tour-core's database. NEVER a Flyway migration.
--
--   docker compose exec -T tours-db psql -U tours -d tours -v ON_ERROR_STOP=1 -v replace=1 < scripts/seed_tours.sql
--
-- Variables (psql -v):
--   n=100000    number of tours (default 100000)
--   replace=1   delete every existing tour and departure summary first; default appends
--
-- Deterministic: setseed() fixes random(), so two runs with the same n produce the same rows.
-- Most tours cluster around real hubs (Etosha, Sossusvlei, the Okavango, Kruger, Victoria Falls...),
-- the rest are spread uniformly over the four countries' bounding boxes. That gives both dense
-- areas (where radius and distance sorting have work to do) and sparse ones.

\if :{?n}
\else
  \set n 100000
\endif

BEGIN;

\if :{?replace}
TRUNCATE tours RESTART IDENTITY CASCADE;
TRUNCATE tour_departure_summary;
\endif

SELECT setseed(0.4242);

CREATE TEMP TABLE seed_hubs (idx int, country text, place text, lat float8, lon float8) ON COMMIT DROP;
INSERT INTO seed_hubs
SELECT row_number() OVER (), * FROM (VALUES
    ('NA', 'Sossusvlei',        -24.7275, 15.3424), ('NA', 'Swakopmund',     -22.6784, 14.5266),
    ('NA', 'Etosha',            -18.8556, 16.3294), ('NA', 'Windhoek',       -22.5609, 17.0658),
    ('NA', 'Damaraland',        -20.5000, 14.4000), ('NA', 'Fish River Canyon', -27.5914, 17.6103),
    ('NA', 'Kaokoland',         -18.0500, 13.2500), ('NA', 'Zambezi Region', -17.5000, 24.2700),
    ('NA', 'Luderitz',          -26.6481, 15.1594), ('NA', 'Walvis Bay',     -22.9575, 14.5053),
    ('NA', 'Spitzkoppe',        -21.8270, 15.1950), ('NA', 'Waterberg',      -20.5130, 17.2400),
    ('BW', 'Okavango Delta',    -19.3000, 22.9000), ('BW', 'Chobe',          -18.6000, 24.5000),
    ('BW', 'Makgadikgadi Pans', -20.7500, 25.5000), ('BW', 'Maun',           -19.9833, 23.4167),
    ('BW', 'Central Kalahari',  -22.0000, 23.5000), ('BW', 'Tuli Block',     -22.1667, 29.1667),
    ('ZA', 'Cape Town',         -33.9249, 18.4241), ('ZA', 'Kruger',         -24.0000, 31.5000),
    ('ZA', 'Drakensberg',       -29.4000, 29.4000), ('ZA', 'Garden Route',   -33.9500, 22.4500),
    ('ZA', 'Stellenbosch',      -33.9321, 18.8602), ('ZA', 'Durban',         -29.8587, 31.0218),
    ('ZA', 'Johannesburg',      -26.2041, 28.0473), ('ZA', 'Hermanus',       -34.4187, 19.2345),
    ('ZA', 'Cederberg',         -32.3500, 19.1000), ('ZA', 'iSimangaliso',   -27.8500, 32.5500),
    ('ZM', 'Victoria Falls',    -17.9243, 25.8572), ('ZM', 'South Luangwa',  -13.0833, 31.5000),
    ('ZM', 'Lower Zambezi',     -15.5000, 29.5000), ('ZM', 'Kafue',          -15.0000, 25.9000),
    ('ZM', 'Lusaka',            -15.3875, 28.3228), ('ZM', 'Lake Kariba',    -16.9500, 28.0000)
) AS h(country, place, lat, lon);

-- Bounding boxes (min lon, min lat, max lon, max lat) for the uniformly spread share.
CREATE TEMP TABLE seed_boxes (country text, min_lon float8, min_lat float8, max_lon float8, max_lat float8)
    ON COMMIT DROP;
INSERT INTO seed_boxes VALUES
    ('NA', 11.72, -28.97, 25.26, -16.96),
    ('BW', 19.99, -26.91, 29.38, -17.78),
    ('ZA', 16.45, -34.84, 32.89, -22.13),
    ('ZM', 21.99, -18.08, 33.71, -8.22);

INSERT INTO tours (name, description, price, currency, location, category, difficulty, duration_minutes,
                   rating_sum, rating_count, created_at, updated_at)
SELECT
    left(r.adjective || ' ' || h.place || ' ' || r.activity, 200),
    format('A %s %s tour around %s. %s %s',
           CASE WHEN r.duration_minutes >= 1440 THEN (r.duration_minutes / 1440) || '-day'
                ELSE (r.duration_minutes / 60) || '-hour' END,
           lower(r.category), h.place, r.highlight, r.extra),
    r.price,
    'NAD',
    ST_SetSRID(ST_MakePoint(r.lon, r.lat), 4326),
    r.category,
    r.difficulty,
    r.duration_minutes,
    -- rating_sum within [count, 5 * count], from an average between roughly 2.5 and 5.0
    least(5 * r.rating_count, greatest(r.rating_count, round(r.rating_count * r.avg_target)::bigint)),
    r.rating_count,
    r.created_at,
    r.created_at
FROM generate_series(1, :n) AS g(i)
CROSS JOIN LATERAL (
    SELECT g.i AS i,
           1 + floor(random() * (SELECT count(*) FROM seed_hubs))::int AS hub,
           random() < 0.75 AS clustered,
           (ARRAY['SAFARI', 'DESERT', 'COASTAL', 'CULTURAL', 'ADVENTURE', 'CITY', 'WILDLIFE', 'PHOTOGRAPHY'])
               [1 + floor(random() * 8)::int] AS category,
           random() AS u_difficulty, random() AS u_duration, random() AS u_days, random() AS u_price,
           random() AS u_rated, random() AS u_count, random() AS u_avg, random() AS u_age,
           random() AS u_adj, random() AS u_act, random() AS u_hl, random() AS u_extra,
           random() AS u_x, random() AS u_y, random() AS u_box
) AS u
JOIN seed_hubs h ON h.idx = u.hub
CROSS JOIN LATERAL (
    SELECT
        u.category,
        CASE WHEN u.u_difficulty < 0.40 THEN 'EASY' WHEN u.u_difficulty < 0.75 THEN 'MODERATE'
             WHEN u.u_difficulty < 0.95 THEN 'CHALLENGING' ELSE 'EXTREME' END AS difficulty,
        d.duration_minutes,
        -- NAD 150 to 650 per hour for day tours, 1 800 to 6 500 per day for multi-day trips, to the rand
        round(CASE WHEN d.duration_minutes >= 1440 THEN (d.duration_minutes / 1440) * (1800 + u.u_price * 4700)
                   ELSE greatest(250, (d.duration_minutes / 60.0) * (150 + u.u_price * 500)) END)::numeric(10, 2)
            AS price,
        CASE WHEN u.u_rated < 0.15 THEN 0 ELSE 1 + floor(power(u.u_count, 2) * 400)::int END AS rating_count,
        2.5 + 2.5 * sqrt(u.u_avg) AS avg_target,
        now() - make_interval(secs => u.u_age * 3 * 365 * 86400) AS created_at,
        CASE WHEN u.clustered THEN h.lat + (u.u_y - 0.5) * 1.2
             ELSE b.min_lat + u.u_y * (b.max_lat - b.min_lat) END AS lat,
        CASE WHEN u.clustered THEN h.lon + (u.u_x - 0.5) * 1.2
             ELSE b.min_lon + u.u_x * (b.max_lon - b.min_lon) END AS lon,
        (ARRAY['Sunrise', 'Sunset', 'Private', 'Small-Group', 'Full-Day', 'Classic', 'Luxury', 'Budget',
               'Guided', 'Family'])[1 + floor(u.u_adj * 10)::int] AS adjective,
        (CASE u.category
            WHEN 'SAFARI'      THEN ARRAY['Game Drive', 'Big Five Safari', 'Walking Safari', 'Night Drive']
            WHEN 'DESERT'      THEN ARRAY['Dune Climb', 'Desert Hike', 'Sandboarding', 'Stargazing Camp']
            WHEN 'COASTAL'     THEN ARRAY['Kayak Tour', 'Seal Cruise', 'Shipwreck Coast Drive', 'Beach Ride']
            WHEN 'CULTURAL'    THEN ARRAY['Township Walk', 'Village Visit', 'Heritage Tour', 'Rock Art Trail']
            WHEN 'ADVENTURE'   THEN ARRAY['Quad Bike Ride', 'Skydive', 'Canyon Hike', 'White-Water Rafting']
            WHEN 'CITY'        THEN ARRAY['City Walk', 'Food Tour', 'Craft Beer Tour', 'Market Tour']
            WHEN 'WILDLIFE'    THEN ARRAY['Rhino Tracking', 'Bird Watching', 'Elephant Encounter', 'Cheetah Visit']
            ELSE                    ARRAY['Photo Tour', 'Astrophotography Night', 'Wildlife Photo Safari',
                                          'Landscape Workshop']
         END)[1 + floor(u.u_act * 4)::int] AS activity,
        (ARRAY['Spot lions and elephants at the waterholes.', 'Climb red dunes at dawn.',
               'Paddle among Cape fur seals.', 'Taste local food with a guide who grew up here.',
               'Track rhino on foot with expert trackers.', 'Photograph the Milky Way over the desert.',
               'Watch the sunset from a riverboat.', 'Visit a Himba village and learn about local customs.',
               'Hike through a canyon carved over millions of years.', 'See ancient San rock engravings.',
               'Ride a quad bike over the dunes.', 'Look for leopards on a night drive.'])
            [1 + floor(u.u_hl * 12)::int] AS highlight,
        (ARRAY['Hotel pick-up included.', 'Lunch and drinks included.', 'Small groups of up to eight.',
               'Binoculars provided.', 'Suitable for families.', 'Bring a hat and sunscreen.',
               'Camping equipment provided.', 'English and German speaking guides.'])
            [1 + floor(u.u_extra * 8)::int] AS extra
    FROM (SELECT CASE WHEN u.u_duration < 0.70 THEN (60 * (1 + floor(u.u_days * 8)))::int
                      ELSE (1440 * (1 + floor(u.u_days * 10)))::int END AS duration_minutes) AS d
    CROSS JOIN LATERAL (
        SELECT * FROM seed_boxes WHERE seed_boxes.country = h.country
    ) AS b
) AS r;

-- Departures in the read model, as availability.changed events from booking-service would have left
-- them: 0 to 4 per tour over the next 120 days, 8 to 40 seats, about one in six sold out.
-- Ids start at 10 000 001 so they never collide with departures a running booking-service creates.
INSERT INTO tour_departure_summary (availability_id, tour_id, starts_at, total_slots, slots_left, source_version)
SELECT 10000000 + row_number() OVER (ORDER BY d.tour_id, d.starts_at),
       d.tour_id, d.starts_at, d.total_slots,
       CASE WHEN d.u_left < 0.16 THEN 0 ELSE 1 + floor(d.u_left * d.total_slots)::int % d.total_slots END,
       1
FROM (
    SELECT t.id AS tour_id,
           date_trunc('day', now()) + make_interval(days => 1 + floor(random() * 120)::int, hours => 6)
               AS starts_at,
           8 + floor(random() * 33)::int AS total_slots,
           random() AS u_left
    FROM tours t
    CROSS JOIN LATERAL generate_series(1, floor(random() * 5)::int + 0 * t.id::int) AS n
) AS d
ON CONFLICT (availability_id) DO NOTHING;

COMMIT;

ANALYZE tours;
ANALYZE tour_departure_summary;

SELECT (SELECT count(*) FROM tours) AS tours,
       (SELECT count(*) FROM tours WHERE rating_count > 0) AS rated,
       (SELECT count(*) FROM tour_departure_summary) AS departures,
       (SELECT count(*) FROM tour_departure_summary WHERE slots_left = 0) AS sold_out;
