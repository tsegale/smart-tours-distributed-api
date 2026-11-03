-- What notifications need about a tour (Phase 4): where to meet, and the time zone its departures are in.
-- booking-service snapshots both when a departure is created, so a ticket or reminder never calls back here.
--
-- time_zone is an IANA region id (Africa/Windhoek), never a fixed offset: departures are shown in the
-- tour's local time, and reminders are scheduled by it. Existing tours (all in Namibia and its neighbours,
-- UTC+2 all year) get the default; the API always writes one.
ALTER TABLE tours
    ADD COLUMN meeting_point VARCHAR(500),
    ADD COLUMN time_zone     VARCHAR(64) NOT NULL DEFAULT 'Africa/Windhoek';
