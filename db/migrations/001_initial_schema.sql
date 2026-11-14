-- FireSystem: initial schema for Supabase.
-- Run once in the Supabase SQL editor, top to bottom. Safe to re-run.
-- Design: docs/architecture/firesystem-backend-architecture.html (workspace C:\NASA)


-- 0. Extensions ------------------------------------------------------------

create extension if not exists postgis;


-- 1. Catalog ---------------------------------------------------------------

create table if not exists satellite (
  id          smallint primary key,
  code        text unique not null,     -- snpp, noaa20, noaa21, aqua, terra
  firms_label text unique not null,     -- value of the FIRMS "satellite" column
  instrument  text not null check (instrument in ('MODIS', 'VIIRS')),
  pixel_m     int not null,
  priority    smallint not null         -- 1 = reference satellite
);

insert into satellite (id, code, firms_label, instrument, pixel_m, priority) values
  (1, 'snpp',   'N',     'VIIRS', 375,  1),
  (2, 'noaa20', 'N20',   'VIIRS', 375,  2),
  (3, 'noaa21', 'N21',   'VIIRS', 375,  3),
  (4, 'aqua',   'Aqua',  'MODIS', 1000, 4),
  (5, 'terra',  'Terra', 'MODIS', 1000, 5)
on conflict (id) do nothing;

create table if not exists firms_source (
  code       text primary key,          -- FIRMS product id, e.g. VIIRS_NOAA20_NRT
  instrument text not null check (instrument in ('MODIS', 'VIIRS')),
  kind       text not null check (kind in ('SP', 'NRT')),
  min_date   date,
  max_date   date,
  checked_at timestamptz
);

insert into firms_source (code, instrument, kind) values
  ('MODIS_SP',         'MODIS', 'SP'),
  ('MODIS_NRT',        'MODIS', 'NRT'),
  ('VIIRS_SNPP_SP',    'VIIRS', 'SP'),
  ('VIIRS_SNPP_NRT',   'VIIRS', 'NRT'),
  ('VIIRS_NOAA20_SP',  'VIIRS', 'SP'),
  ('VIIRS_NOAA20_NRT', 'VIIRS', 'NRT'),
  ('VIIRS_NOAA21_SP',  'VIIRS', 'SP'),
  ('VIIRS_NOAA21_NRT', 'VIIRS', 'NRT')
on conflict (code) do nothing;


-- 2. Ingest control --------------------------------------------------------

create table if not exists ingest_batch (
  id            bigint generated always as identity primary key,
  source_code   text not null references firms_source (code),
  mode          text not null check (mode in ('backfill', 'daily')),
  area          text not null,          -- mainland, easter_island, juan_fernandez
  date_from     date not null,
  date_to       date not null,
  rows_received int not null default 0,
  rows_saved    int not null default 0,
  rows_rejected int not null default 0,
  status        text not null default 'running' check (status in ('running', 'ok', 'failed')),
  error         text,
  started_at    timestamptz not null default now(),
  finished_at   timestamptz
);

create index if not exists ingest_batch_lookup on ingest_batch (source_code, area, date_from, date_to, status);


-- 3. Raw detections --------------------------------------------------------

create table if not exists fire_detection (
  id               bigint generated always as identity primary key,
  satellite_id     smallint not null references satellite (id),
  source_code      text not null references firms_source (code),
  batch_id         bigint not null references ingest_batch (id),
  acq_time         timestamptz not null,
  lat              double precision not null check (lat between -90 and 90),
  lon              double precision not null check (lon between -180 and 180),
  confidence_level smallint not null check (confidence_level between 0 and 2),  -- 0 low, 1 nominal, 2 high
  confidence_raw   text not null,
  brightness       real,                -- MODIS brightness or VIIRS bright_ti4
  brightness_2     real,                -- MODIS bright_t31 or VIIRS bright_ti5
  frp              real,
  scan             real,
  track            real,
  daynight         char(1),
  fire_type        smallint,            -- SP only: 0 vegetation, 1 volcano, 2 static source, 3 water
  version          text,
  solar_date date generated always as
    (((acq_time at time zone 'UTC') + make_interval(secs => lon * 240))::date) stored,
  cell_x int generated always as (floor(lon / 0.05)::int) stored,
  cell_y int generated always as (floor(lat / 0.05)::int) stored,
  geom geometry(point, 4326) generated always as (st_setsrid(st_makepoint(lon, lat), 4326)) stored,
  unique (satellite_id, source_code, acq_time, lat, lon)
);

create index if not exists fire_detection_geom on fire_detection using gist (geom);
create index if not exists fire_detection_solar_date on fire_detection (solar_date);
create index if not exists fire_detection_source_time on fire_detection (source_code, acq_time);

create table if not exists excluded_cell (
  cell_x     int,
  cell_y     int,
  reason     text not null check (reason in ('volcano', 'static_source', 'manual')),
  origin     text not null check (origin in ('auto', 'team')),
  note       text,
  created_at timestamptz not null default now(),
  primary key (cell_x, cell_y)
);


-- 4. Summaries -------------------------------------------------------------

create table if not exists fire_cell_day (
  cell_x       int,
  cell_y       int,
  solar_date   date,
  satellite_id smallint references satellite (id),
  detections   int not null,
  frp_sum      real,
  frp_max      real,
  provisional  boolean not null,
  center       geometry(point, 4326) not null,
  primary key (cell_x, cell_y, solar_date, satellite_id)
);

create index if not exists fire_cell_day_center on fire_cell_day using gist (center);
create index if not exists fire_cell_day_date_sat on fire_cell_day (solar_date, satellite_id);

create table if not exists daily_reference (
  solar_date  date primary key,
  reference   text not null check (reference in ('snpp', 'noaa20', 'noaa21', 'modis', 'aqua', 'terra', 'none')),
  is_fallback boolean not null
);

create table if not exists calibration_factor (
  reference         text not null,
  month             smallint not null check (month between 1 and 12),
  k                 real not null,
  overlap_cell_days int not null,
  is_estimated      boolean not null default false,
  method_version    text not null,
  computed_at       timestamptz not null default now(),
  primary key (reference, month)
);


-- 5. Write functions (ingest only) -----------------------------------------

-- Rebuild cell-days and the daily reference for a date range.
create or replace function rebuild_cell_days(d_from date, d_to date) returns void
language sql as $$
  delete from fire_cell_day where solar_date between d_from and d_to;

  insert into fire_cell_day
  select d.cell_x, d.cell_y, d.solar_date, d.satellite_id,
         count(*), sum(d.frp), max(d.frp), bool_or(s.kind = 'NRT'),
         st_setsrid(st_makepoint((d.cell_x + 0.5) * 0.05, (d.cell_y + 0.5) * 0.05), 4326)
  from fire_detection d
  join firms_source s on s.code = d.source_code
  where d.solar_date between d_from and d_to
    and d.confidence_level > 0
    and d.fire_type is distinct from 3
    and not exists (select 1 from excluded_cell e where e.cell_x = d.cell_x and e.cell_y = d.cell_y)
  group by d.cell_x, d.cell_y, d.solar_date, d.satellite_id;

  delete from daily_reference where solar_date between d_from and d_to;

  insert into daily_reference
  select day, ref, ref not in ('snpp', 'none')
  from (
    select g.day::date as day,
      case when bool_or(f.satellite_id = 1) then 'snpp'
           when bool_or(f.satellite_id = 2) then 'noaa20'
           when bool_or(f.satellite_id = 3) then 'noaa21'
           when bool_or(f.satellite_id = 4) and bool_or(f.satellite_id = 5) then 'modis'
           when bool_or(f.satellite_id = 4) then 'aqua'
           when bool_or(f.satellite_id = 5) then 'terra'
           else 'none' end as ref
    from generate_series(d_from, d_to, interval '1 day') as g(day)
    left join fire_cell_day f on f.solar_date = g.day::date
    group by g.day
  ) as x;
$$;

-- When SP arrives, delete the NRT rows it replaces.
create or replace function replace_nrt_with_sp(nrt_code text, d_from date, d_to date) returns void
language sql as $$
  delete from fire_detection
  where source_code = nrt_code
    and acq_time >= d_from
    and acq_time < d_to + 1;
$$;

-- Volcanoes and static sources: cells flagged 3 or more times in SP data.
create or replace function refresh_excluded_cells() returns void
language sql as $$
  insert into excluded_cell (cell_x, cell_y, reason, origin)
  select cell_x, cell_y,
         case when min(fire_type) = 1 then 'volcano' else 'static_source' end,
         'auto'
  from fire_detection
  where fire_type in (1, 2)
  group by cell_x, cell_y
  having count(*) >= 3
  on conflict (cell_x, cell_y) do nothing;
$$;

-- k = S-NPP cell-days / group cell-days, on days where both saw fire.
create or replace function recompute_calibration() returns void
language sql as $$
  delete from calibration_factor;

  insert into calibration_factor (reference, month, k, overlap_cell_days, is_estimated, method_version)
  with grp (reference, sats) as (
    values ('noaa20', array[2]::smallint[]),
           ('noaa21', array[3]::smallint[]),
           ('modis',  array[4, 5]::smallint[]),
           ('aqua',   array[4]::smallint[]),
           ('terra',  array[5]::smallint[])
  ),
  days as (
    select g.reference, g.sats, f.solar_date
    from grp g
    join fire_cell_day f on not f.provisional
    group by g.reference, g.sats, f.solar_date
    having bool_or(f.satellite_id = 1)
       and count(distinct f.satellite_id) filter (where f.satellite_id = any (g.sats)) = cardinality(g.sats)
  ),
  per_day as (
    select d.reference,
           extract(month from d.solar_date)::int as month,
           (select count(*) from fire_cell_day f
             where f.solar_date = d.solar_date and f.satellite_id = 1) as n_ref,
           (select count(distinct (f.cell_x, f.cell_y)) from fire_cell_day f
             where f.solar_date = d.solar_date and f.satellite_id = any (d.sats)) as n_grp
    from days d
  ),
  monthly as (
    select reference, month, sum(n_ref) as n_ref, sum(n_grp) as n_grp
    from per_day
    group by reference, month
  ),
  yearly as (
    select reference, sum(n_ref)::real / nullif(sum(n_grp), 0) as k_year
    from monthly
    group by reference
  )
  select m.reference, m.month,
         case when m.n_grp >= 30 then m.n_ref::real / m.n_grp else y.k_year end,
         m.n_grp, m.n_grp < 30, 'v1'
  from monthly m
  join yearly y using (reference)
  where y.k_year is not null;

  insert into calibration_factor (reference, month, k, overlap_cell_days, is_estimated, method_version)
  select 'snpp', m, 1, 0, false, 'v1'
  from generate_series(1, 12) as m;
$$;


-- 6. Read functions (API) --------------------------------------------------

-- Calendar: reference-satellite cell-days per year, month and reference.
create or replace function calendar_counts(aoi jsonb, d_from date, d_to date, mtd_day int)
returns table (year int, month int, reference text, cell_days bigint, cell_days_mtd bigint, provisional boolean)
language sql stable as $$
  with area as (
    select st_makevalid(st_setsrid(st_geomfromgeojson(aoi::text), 4326)) as g
  ),
  hits as (
    select r.solar_date, r.reference, f.cell_x, f.cell_y, bool_or(f.provisional) as prov
    from daily_reference r
    join fire_cell_day f on f.solar_date = r.solar_date
    join satellite s on s.id = f.satellite_id
    cross join area
    where r.solar_date between d_from and d_to
      and r.reference <> 'none'
      and (s.code = r.reference or (r.reference = 'modis' and s.instrument = 'MODIS'))
      and case when st_area(area.g) < 0.0025   -- area smaller than one cell
               then st_intersects(st_makeenvelope(f.cell_x * 0.05, f.cell_y * 0.05,
                                                  (f.cell_x + 1) * 0.05, (f.cell_y + 1) * 0.05, 4326), area.g)
               else st_intersects(f.center, area.g) end
    group by r.solar_date, r.reference, f.cell_x, f.cell_y
  )
  select extract(year from solar_date)::int,
         extract(month from solar_date)::int,
         reference,
         count(*),
         count(*) filter (where extract(day from solar_date) <= mtd_day),
         bool_or(prov)
  from hits
  group by 1, 2, 3;
$$;

-- Map and CSV: detections in an area, paged by id.
create or replace function hotspots_page(aoi jsonb, d_from date, d_to date, after_id bigint, page_size int)
returns table (id bigint, acq_time timestamptz, solar_date date, lat double precision, lon double precision,
               satellite text, instrument text, confidence_level smallint, frp real,
               source_kind text, excluded boolean)
language sql stable as $$
  select d.id, d.acq_time, d.solar_date, d.lat, d.lon, s.code, s.instrument, d.confidence_level, d.frp, fs.kind,
         d.confidence_level = 0
           or d.fire_type is not distinct from 3
           or exists (select 1 from excluded_cell e where e.cell_x = d.cell_x and e.cell_y = d.cell_y)
  from fire_detection d
  join satellite s on s.id = d.satellite_id
  join firms_source fs on fs.code = d.source_code
  where d.solar_date between d_from and d_to
    and d.id > coalesce(after_id, 0)
    and st_intersects(d.geom, st_makevalid(st_setsrid(st_geomfromgeojson(aoi::text), 4326)))
  order by d.id
  limit least(page_size, 1000);
$$;

create or replace function hotspots_count(aoi jsonb, d_from date, d_to date) returns bigint
language sql stable as $$
  select count(*)
  from fire_detection d
  where d.solar_date between d_from and d_to
    and st_intersects(d.geom, st_makevalid(st_setsrid(st_geomfromgeojson(aoi::text), 4326)));
$$;

-- Data sources page: first and last day each satellite has data.
create or replace function satellite_coverage()
returns table (satellite text, first_date date, last_date date)
language sql stable as $$
  select s.code, min(f.solar_date), max(f.solar_date)
  from fire_cell_day f
  join satellite s on s.id = f.satellite_id
  group by s.code;
$$;

-- Validation: monthly S-NPP vs group cell-days on overlap days (API computes the error).
create or replace function calibration_check()
returns table (reference text, year int, month int, n_ref bigint, n_grp bigint)
language sql stable as $$
  with grp (reference, sats) as (
    values ('noaa20', array[2]::smallint[]),
           ('noaa21', array[3]::smallint[]),
           ('modis',  array[4, 5]::smallint[])
  ),
  days as (
    select g.reference, g.sats, f.solar_date
    from grp g
    join fire_cell_day f on not f.provisional
    group by g.reference, g.sats, f.solar_date
    having bool_or(f.satellite_id = 1)
       and count(distinct f.satellite_id) filter (where f.satellite_id = any (g.sats)) = cardinality(g.sats)
  )
  select d.reference,
         extract(year from d.solar_date)::int,
         extract(month from d.solar_date)::int,
         sum((select count(*) from fire_cell_day f
               where f.solar_date = d.solar_date and f.satellite_id = 1))::bigint,
         sum((select count(distinct (f.cell_x, f.cell_y)) from fire_cell_day f
               where f.solar_date = d.solar_date and f.satellite_id = any (d.sats)))::bigint
  from days d
  group by 1, 2, 3;
$$;


-- 7. Permissions: the API (anon) only reads --------------------------------

alter table satellite          enable row level security;
alter table firms_source       enable row level security;
alter table ingest_batch       enable row level security;
alter table fire_detection     enable row level security;
alter table excluded_cell      enable row level security;
alter table fire_cell_day      enable row level security;
alter table daily_reference    enable row level security;
alter table calibration_factor enable row level security;

drop policy if exists anon_read on satellite;
drop policy if exists anon_read on firms_source;
drop policy if exists anon_read on ingest_batch;
drop policy if exists anon_read on fire_detection;
drop policy if exists anon_read on excluded_cell;
drop policy if exists anon_read on fire_cell_day;
drop policy if exists anon_read on daily_reference;
drop policy if exists anon_read on calibration_factor;

create policy anon_read on satellite          for select to anon using (true);
create policy anon_read on firms_source       for select to anon using (true);
create policy anon_read on ingest_batch       for select to anon using (true);
create policy anon_read on fire_detection     for select to anon using (true);
create policy anon_read on excluded_cell      for select to anon using (true);
create policy anon_read on fire_cell_day      for select to anon using (true);
create policy anon_read on daily_reference    for select to anon using (true);
create policy anon_read on calibration_factor for select to anon using (true);
-- No insert/update/delete policies: anon cannot write.

revoke execute on function rebuild_cell_days(date, date)          from public, anon, authenticated;
revoke execute on function replace_nrt_with_sp(text, date, date)  from public, anon, authenticated;
revoke execute on function refresh_excluded_cells()               from public, anon, authenticated;
revoke execute on function recompute_calibration()                from public, anon, authenticated;

grant execute on function rebuild_cell_days(date, date)          to service_role;
grant execute on function replace_nrt_with_sp(text, date, date)  to service_role;
grant execute on function refresh_excluded_cells()               to service_role;
grant execute on function recompute_calibration()                to service_role;

grant execute on function calendar_counts(jsonb, date, date, int)          to anon;
grant execute on function hotspots_page(jsonb, date, date, bigint, int)    to anon;
grant execute on function hotspots_count(jsonb, date, date)                to anon;
grant execute on function satellite_coverage()                             to anon;
grant execute on function calibration_check()                              to anon;


-- 8. Checks (run after the script; each should return rows) ----------------

-- select * from satellite;                                   -- 5 rows
-- select code, kind from firms_source;                       -- 8 rows
-- select tablename, rowsecurity from pg_tables where schemaname = 'public';
-- select * from calendar_counts('{"type":"Polygon","coordinates":[[[-73,-38],[-72,-38],[-72,-37],[-73,-37],[-73,-38]]]}', '2024-01-01', '2024-12-31', 31);
