create extension if not exists postgis with schema extensions;
set search_path = public, extensions;

create or replace function rebuild_cell_days(d_from date, d_to date) returns void
language sql as $$
  delete from fire_cell_day where solar_date between d_from and d_to;

  insert into fire_cell_day (cell_x, cell_y, solar_date, satellite_id, detections, frp_sum, frp_max, provisional)
  select d.cell_x, d.cell_y, d.solar_date, d.satellite_id,
         count(*), sum(d.frp), max(d.frp), bool_or(s.kind = 'NRT')
  from fire_detection d
  join firms_source s on s.code = d.source_code
  where d.solar_date between d_from and d_to
    and d.confidence_level > 0
    and d.fire_type is distinct from 3
    and not exists (select 1 from excluded_cell e where e.cell_x = d.cell_x and e.cell_y = d.cell_y)
  group by d.cell_x, d.cell_y, d.solar_date, d.satellite_id;

  delete from daily_reference where solar_date between d_from and d_to;

  insert into daily_reference (solar_date, reference, is_fallback)
  select solar_date,
         case when bool_or(satellite_id = 1) then 'snpp'
              when bool_or(satellite_id = 2) then 'noaa20'
              when bool_or(satellite_id = 3) then 'noaa21'
              when bool_or(satellite_id = 4) and bool_or(satellite_id = 5) then 'modis'
              when bool_or(satellite_id = 4) then 'aqua'
              else 'terra' end,
         not bool_or(satellite_id = 1)
  from fire_cell_day
  where solar_date between d_from and d_to
  group by solar_date;
$$;

create or replace function replace_nrt_with_sp(nrt_code text, d_from date, d_to date) returns void
language sql as $$
  delete from fire_detection
  where source_code = nrt_code
    and acq_time >= d_from
    and acq_time < d_to + 1;
$$;

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

create or replace function calibration_day()
returns table (reference text, solar_date date, n_ref bigint, n_grp bigint)
language sql stable as $$
  with grp (reference, sats) as (
    values ('noaa20', array[2]::smallint[]),
           ('noaa21', array[3]::smallint[]),
           ('modis',  array[4, 5]::smallint[]),
           ('aqua',   array[4]::smallint[]),
           ('terra',  array[5]::smallint[])
  )
  select g.reference, f.solar_date,
         count(*) filter (where f.satellite_id = 1),
         count(distinct (f.cell_x, f.cell_y)) filter (where f.satellite_id = any (g.sats))
  from grp g
  cross join fire_cell_day f
  where not f.provisional
  group by g.reference, f.solar_date
  having count(*) filter (where f.satellite_id = 1) > 0
     and count(*) filter (where f.satellite_id = any (g.sats)) > 0;
$$;

create or replace function recompute_calibration() returns void
language sql as $$
  delete from calibration_factor;

  insert into calibration_factor (reference, month, k, overlap_cell_days, is_estimated, method_version)
  with monthly as (
    select reference, extract(month from solar_date)::int as month, sum(n_ref) as n_ref, sum(n_grp) as n_grp
    from calibration_day()
    group by reference, extract(month from solar_date)
  ),
  yearly as (
    select reference, sum(n_ref) / sum(n_grp) as k_year
    from monthly
    group by reference
  )
  select m.reference, m.month,
         case when m.n_grp >= 30 then m.n_ref / m.n_grp else y.k_year end,
         m.n_grp, m.n_grp < 30, 'v1'
  from monthly m
  join yearly y using (reference);

  insert into calibration_factor (reference, month, k, overlap_cell_days, is_estimated, method_version)
  select 'snpp', m, 1, 0, false, 'v1'
  from generate_series(1, 12) as m;
$$;

create or replace function aoi_area(aoi jsonb)
returns table (g geometry, x0 int, x1 int, y0 int, y1 int)
language sql immutable set search_path = public, extensions as $$
  select p.g,
         floor(st_xmin(p.g) / 0.05)::int, floor(st_xmax(p.g) / 0.05)::int,
         floor(st_ymin(p.g) / 0.05)::int, floor(st_ymax(p.g) / 0.05)::int
  from (select st_makevalid(st_setsrid(st_geomfromgeojson(aoi::text), 4326)) as g) as p;
$$;

create or replace function calendar_counts(aoi jsonb, d_from date, d_to date, mtd_day int)
returns table (year int, month int, reference text, cell_days bigint, cell_days_mtd bigint, provisional boolean)
language sql stable set search_path = public, extensions as $$
  with hits as (
    select r.solar_date, r.reference, bool_or(f.provisional) as prov
    from aoi_area(aoi) as a
    join fire_cell_day f on f.cell_x between a.x0 and a.x1 and f.cell_y between a.y0 and a.y1
    join daily_reference r on r.solar_date = f.solar_date
    join satellite s on s.id = f.satellite_id
    where f.solar_date between d_from and d_to
      and (s.code = r.reference or (r.reference = 'modis' and s.instrument = 'MODIS'))
      and st_intersects(
            st_makeenvelope(f.cell_x * 0.05, f.cell_y * 0.05, (f.cell_x + 1) * 0.05, (f.cell_y + 1) * 0.05, 4326),
            a.g)
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

create or replace function hotspots_page(aoi jsonb, d_from date, d_to date, after_id bigint, page_size int)
returns table (id bigint, acq_time timestamptz, solar_date date, lat double precision, lon double precision,
               satellite text, instrument text, confidence_level smallint, frp real,
               source_kind text, excluded boolean)
language sql stable set search_path = public, extensions as $$
  select d.id, d.acq_time, d.solar_date, d.lat, d.lon, s.code, s.instrument, d.confidence_level, d.frp, fs.kind,
         d.confidence_level = 0
           or d.fire_type is not distinct from 3
           or exists (select 1 from excluded_cell e where e.cell_x = d.cell_x and e.cell_y = d.cell_y)
  from aoi_area(aoi) as a
  join fire_detection d on d.cell_x between a.x0 and a.x1 and d.cell_y between a.y0 and a.y1
  join satellite s on s.id = d.satellite_id
  join firms_source fs on fs.code = d.source_code
  where d.solar_date between d_from and d_to
    and d.id > coalesce(after_id, 0)
    and st_intersects(st_setsrid(st_makepoint(d.lon, d.lat), 4326), a.g)
  order by d.id
  limit least(page_size, 1000);
$$;

create or replace function hotspots_count(aoi jsonb, d_from date, d_to date) returns bigint
language sql stable set search_path = public, extensions as $$
  select count(*)
  from aoi_area(aoi) as a
  join fire_detection d on d.cell_x between a.x0 and a.x1 and d.cell_y between a.y0 and a.y1
  where d.solar_date between d_from and d_to
    and st_intersects(st_setsrid(st_makepoint(d.lon, d.lat), 4326), a.g);
$$;

create or replace function satellite_coverage()
returns table (satellite text, first_date date, last_date date)
language sql stable as $$
  select s.code, min(f.solar_date), max(f.solar_date)
  from fire_cell_day f
  join satellite s on s.id = f.satellite_id
  group by s.code;
$$;

create or replace function calibration_check()
returns table (reference text, year int, month int, n_ref bigint, n_grp bigint)
language sql stable as $$
  select reference,
         extract(year from solar_date)::int,
         extract(month from solar_date)::int,
         sum(n_ref)::bigint,
         sum(n_grp)::bigint
  from calibration_day()
  where reference in ('noaa20', 'noaa21', 'modis')
  group by 1, 2, 3;
$$;

alter table satellite enable row level security;
alter table firms_source enable row level security;
alter table ingest_batch enable row level security;
alter table fire_detection enable row level security;
alter table excluded_cell enable row level security;
alter table fire_cell_day enable row level security;
alter table daily_reference enable row level security;
alter table calibration_factor enable row level security;

create policy anon_read on satellite for select to anon using (true);
create policy anon_read on firms_source for select to anon using (true);
create policy anon_read on ingest_batch for select to anon using (true);
create policy anon_read on fire_detection for select to anon using (true);
create policy anon_read on excluded_cell for select to anon using (true);
create policy anon_read on fire_cell_day for select to anon using (true);
create policy anon_read on daily_reference for select to anon using (true);
create policy anon_read on calibration_factor for select to anon using (true);

revoke insert, update, delete, truncate on all tables in schema public from anon, authenticated;
grant select on all tables in schema public to anon;

revoke execute on function rebuild_cell_days(date, date), replace_nrt_with_sp(text, date, date),
  refresh_excluded_cells(), recompute_calibration() from public, anon, authenticated;
grant execute on function rebuild_cell_days(date, date), replace_nrt_with_sp(text, date, date),
  refresh_excluded_cells(), recompute_calibration() to service_role;
grant execute on function aoi_area(jsonb), calibration_day(), calendar_counts(jsonb, date, date, int),
  hotspots_page(jsonb, date, date, bigint, int), hotspots_count(jsonb, date, date),
  satellite_coverage(), calibration_check() to anon;
