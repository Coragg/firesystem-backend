-- [AI] Comments tagged [AI] were added by an AI assistant to describe the schema; no statement was changed.
-- [AI] Tables: reference data (satellite, firms_source), raw detections and their ingest log
-- (fire_detection, ingest_batch), and summaries rebuilt by the SQL functions in 002 (excluded_cell,
-- fire_cell_day, daily_reference, calibration_factor).

-- [AI] The five satellites. priority sets which one becomes a day's reference in rebuild_cell_days:
-- S-NPP (1) first, then NOAA-20, NOAA-21, and MODIS Aqua/Terra last.
create table if not exists satellite (
  id smallint primary key,
  code text unique not null,
  firms_label text unique not null,
  instrument text not null check (instrument in ('MODIS', 'VIIRS')),
  pixel_m int not null,
  priority smallint not null
);

insert into satellite (id, code, firms_label, instrument, pixel_m, priority) values
  (1, 'snpp', 'N', 'VIIRS', 375, 1),
  (2, 'noaa20', 'N20', 'VIIRS', 375, 2),
  (3, 'noaa21', 'N21', 'VIIRS', 375, 3),
  (4, 'aqua', 'Aqua', 'MODIS', 1000, 4),
  (5, 'terra', 'Terra', 'MODIS', 1000, 5)
on conflict (id) do nothing;

-- [AI] FIRMS products (e.g. VIIRS_SNPP_SP) with the date range FIRMS reports. SP = standard processing
-- (final), NRT = near real time (provisional, replaced by SP later). Refreshed on every ingest run.
create table if not exists firms_source (
  code text primary key,
  instrument text not null check (instrument in ('MODIS', 'VIIRS')),
  kind text not null check (kind in ('SP', 'NRT')),
  min_date date,
  max_date date,
  checked_at timestamptz
);

-- [AI] One row per downloaded chunk (source x area x up to 5 days). Lets backfill skip chunks already
-- loaded, and its latest successful finished_at is the "data updated at" time the API reports.
create table if not exists ingest_batch (
  id bigint generated always as identity primary key,
  source_code text not null references firms_source (code),
  mode text not null check (mode in ('backfill', 'daily')),
  area text not null,
  date_from date not null,
  date_to date not null,
  rows_received int not null default 0,
  rows_saved int not null default 0,
  rows_rejected int not null default 0,
  status text not null default 'running' check (status in ('running', 'ok', 'failed')),
  error text,
  started_at timestamptz not null default now(),
  finished_at timestamptz
);

-- [AI] Serves the already_loaded() check in src/ingest/run.py.
create index if not exists ingest_batch_lookup on ingest_batch (source_code, area, date_from, date_to, status);

-- [AI] Every MODIS / VIIRS hotspot, normalized by src/ingest/normalize.py.
-- solar_date = the local solar day (UTC time shifted by longitude / 15 hours), so days follow local
-- time rather than UTC. cell_x / cell_y = floor(lon or lat / 0.05): the ~5 km grid used for counting.
-- confidence_level: 0 low, 1 nominal, 2 high (MODIS 0-100 and VIIRS l/n/h mapped onto one scale).
create table if not exists fire_detection (
  id bigint generated always as identity primary key,
  satellite_id smallint not null references satellite (id),
  source_code text not null references firms_source (code),
  batch_id bigint not null references ingest_batch (id),
  acq_time timestamptz not null,
  solar_date date not null,
  lat double precision not null check (lat between -90 and 90),
  lon double precision not null check (lon between -180 and 180),
  cell_x int not null,
  cell_y int not null,
  confidence_level smallint not null check (confidence_level between 0 and 2),
  confidence_raw text not null,
  brightness real,
  brightness_2 real,
  frp real,
  scan real,
  track real,
  daynight char(1),
  fire_type smallint,
  version text,
  -- [AI] Natural key of a detection; the ingest upserts on it so reloading the same days adds no duplicates.
  unique (satellite_id, source_code, acq_time, lat, lon)
);

create index if not exists fire_detection_solar_date on fire_detection (solar_date);

create index if not exists fire_detection_source_time on fire_detection (source_code, acq_time);

-- [AI] Grid cells left out of the counts: volcanoes and other static land sources (FIRMS fire types 1 and 2),
-- found automatically by refresh_excluded_cells() or added by hand (origin 'team').
create table if not exists excluded_cell (
  cell_x int,
  cell_y int,
  reason text not null check (reason in ('volcano', 'static_source', 'manual')),
  origin text not null check (origin in ('auto', 'team')),
  note text,
  created_at timestamptz not null default now(),
  primary key (cell_x, cell_y)
);

-- [AI] Summary: one row per cell, solar day and satellite with at least one counted detection. The
-- calendar's unit, the "cell-day", is one row here. Rebuilt by rebuild_cell_days(); never edited by hand.
create table if not exists fire_cell_day (
  cell_x int,
  cell_y int,
  solar_date date,
  satellite_id smallint references satellite (id),
  detections int not null,
  frp_sum real,
  frp_max real,
  provisional boolean not null,
  primary key (cell_x, cell_y, solar_date, satellite_id)
);

create index if not exists fire_cell_day_date_sat on fire_cell_day (solar_date, satellite_id);

-- [AI] Which satellite's counts represent each solar day ('modis' = Aqua and Terra together).
-- is_fallback is true when S-NPP had nothing that day and a substitute was used.
create table if not exists daily_reference (
  solar_date date primary key,
  reference text not null check (reference in ('snpp', 'noaa20', 'noaa21', 'modis', 'aqua', 'terra')),
  is_fallback boolean not null
);

-- [AI] Factor k per reference and calendar month: S-NPP cell-days ~= k x reference cell-days.
-- Computed by recompute_calibration() from days both were observed; used by src/utils/harmonize.py.
create table if not exists calibration_factor (
  reference text not null,
  month smallint not null check (month between 1 and 12),
  k real not null,
  overlap_cell_days int not null,
  is_estimated boolean not null default false,
  method_version text not null,
  computed_at timestamptz not null default now(),
  primary key (reference, month)
);
