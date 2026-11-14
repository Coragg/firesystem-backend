import argparse
import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import get_args

import httpx
from supabase import Client

from src.config import LOG_LEVEL, SUPABASE_INGEST_KEY, SUPABASE_URL
from src.ingest.firms import fetch_area_csv, fetch_availability
from src.ingest.normalize import FirmsResponseError, parse_csv
from src.models.nasa import FirmsSource
from src.utils import firms as firms_util
from src.utils.coverage import AREAS
from src.utils.supabase import create_supabase_client

logger = logging.getLogger("firesystem.ingest")

KNOWN_SOURCES = set(get_args(FirmsSource))  # the team's list: src/models/nasa.py
CHUNK_DAYS = 5  # FIRMS allows 1-5 days per area request
UPSERT_BATCH = 1000
PAUSE_SECONDS = 0.25  # about 2400 calls per 10 minutes, under the FIRMS limit of 5000
ACTIVE_WITHIN_DAYS = 3  # an NRT product that ended earlier than this is skipped
DAILY_WINDOW_DAYS = 5


def chunks(d_from: date, d_to: date, days: int = CHUNK_DAYS) -> list[tuple[date, int]]:
    """Split a date range into (start, length) pieces of at most `days` days."""
    pieces = []
    current = d_from
    while current <= d_to:
        length = min(days, (d_to - current).days + 1)
        pieces.append((current, length))
        current += timedelta(days=length)
    return pieces


def active_nrt_sources(sources: list[dict], today: date) -> list[str]:
    """NRT products still receiving data (S-NPP NRT stopped on 2026-11-02, for example)."""
    cutoff = today - timedelta(days=ACTIVE_WITHIN_DAYS)
    return [
        s["code"] for s in sources
        if s["kind"] == "NRT" and s["max_date"] and date.fromisoformat(s["max_date"]) >= cutoff
    ]


def _describe(exc: Exception) -> str:
    """Error text that is safe to store: the FIRMS key is part of every request URL."""
    return str(exc).replace(firms_util.FIRMS_MAP_KEY, "***")[:500]


def refresh_sources(client: Client, http: httpx.Client, sleep: Callable[[float], None]) -> None:
    """Create or update a firms_source row for every known product FIRMS lists, with its date range."""
    now = datetime.now(UTC).isoformat()
    rows = [
        {
            "code": row["data_id"],
            "instrument": "MODIS" if row["data_id"].startswith("MODIS") else "VIIRS",
            "kind": "SP" if row["data_id"].endswith("_SP") else "NRT",
            "min_date": row["min_date"],
            "max_date": row["max_date"],
            "checked_at": now,
        }
        for row in fetch_availability(http, sleep)
        if row["data_id"] in KNOWN_SOURCES
    ]
    if not rows:
        # Without source rows every later step would quietly load nothing.
        raise FirmsResponseError("FIRMS did not list any known product (is the map key valid?)")
    client.table("firms_source").upsert(rows, on_conflict="code").execute()


def already_loaded(client: Client, source: str, area: str, start: date, end: date) -> bool:
    rows = (
        client.table("ingest_batch").select("id")
        .eq("source_code", source).eq("area", area).eq("date_from", start.isoformat())
        .eq("date_to", end.isoformat()).eq("status", "ok").limit(1).execute().data
    )
    return bool(rows)


def load_chunk(
    client: Client, http: httpx.Client, source: str, area: str, mode: str, start: date, days: int,
    sleep: Callable[[float], None],
) -> bool:
    """Download one source/area/date chunk and upsert it, recording the outcome in ingest_batch."""
    end = start + timedelta(days=days - 1)
    batch_id = client.table("ingest_batch").insert({
        "source_code": source, "mode": mode, "area": area,
        "date_from": start.isoformat(), "date_to": end.isoformat(),
    }).execute().data[0]["id"]
    received = saved = rejected = 0
    try:
        text = fetch_area_csv(http, source, AREAS[area], start, days, sleep)
        detections, rejected = parse_csv(text, source)
        received = len(detections) + rejected
        for i in range(0, len(detections), UPSERT_BATCH):
            rows = [{**d, "batch_id": batch_id} for d in detections[i:i + UPSERT_BATCH]]
            client.table("fire_detection").upsert(
                rows, on_conflict="satellite_id,source_code,acq_time,lat,lon", ignore_duplicates=True
            ).execute()
            saved += len(rows)
    except Exception as exc:
        client.table("ingest_batch").update({
            "status": "failed", "error": _describe(exc), "rows_received": received, "rows_saved": saved,
            "rows_rejected": rejected, "finished_at": datetime.now(UTC).isoformat(),
        }).eq("id", batch_id).execute()
        logger.error("%s %s %s failed: %s", source, area, start, _describe(exc))
        if isinstance(exc, FirmsResponseError):
            raise  # a bad key or quota page affects every chunk: stop the whole run
        return False
    client.table("ingest_batch").update({
        "status": "ok", "rows_received": received, "rows_saved": saved, "rows_rejected": rejected,
        "finished_at": datetime.now(UTC).isoformat(),
    }).eq("id", batch_id).execute()
    logger.info("%s %s %s +%dd: %d rows", source, area, start, days, saved)
    return True


def rebuild_by_year(client: Client, d_from: date, d_to: date) -> None:
    """Rebuild summaries one calendar year at a time to stay under the database statement timeout."""
    for year in range(d_from.year, d_to.year + 1):
        client.rpc("rebuild_cell_days", {
            "d_from": max(d_from, date(year, 1, 1)).isoformat(),
            "d_to": min(d_to, date(year, 12, 31)).isoformat(),
        }).execute()


def daily(client: Client, http: httpx.Client, today: date, sleep: Callable[[float], None] = time.sleep) -> int:
    """Load the latest days of every NRT product still active, then refresh the recent summaries."""
    refresh_sources(client, http, sleep)
    sources = client.table("firms_source").select("*").execute().data
    start = today - timedelta(days=DAILY_WINDOW_DAYS - 1)
    failed = False
    for source in active_nrt_sources(sources, today):
        for area in AREAS:
            failed |= not load_chunk(client, http, source, area, "daily", start, DAILY_WINDOW_DAYS, sleep)
            sleep(PAUSE_SECONDS)
    rebuild_by_year(client, today - timedelta(days=DAILY_WINDOW_DAYS), today)
    return 1 if failed else 0


def backfill(
    client: Client, http: httpx.Client, only_source: str | None = None, d_from: date | None = None,
    d_to: date | None = None, sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Load the Standard Processing archive, skipping chunks already loaded, then rebuild everything."""
    refresh_sources(client, http, sleep)
    rows = client.table("firms_source").select("*").execute().data
    sources = [
        s for s in rows
        if s["kind"] == "SP" and s["min_date"] and s["max_date"] and only_source in (None, s["code"])
    ]
    ranges = {
        s["code"]: (max(date.fromisoformat(s["min_date"]), d_from or date.min),
                    min(date.fromisoformat(s["max_date"]), d_to or date.max))
        for s in sources
    }
    failed = False
    for code, (low, high) in ranges.items():
        for area in AREAS:
            for start, days in chunks(low, high):
                if already_loaded(client, code, area, start, start + timedelta(days=days - 1)):
                    continue
                failed |= not load_chunk(client, http, code, area, "backfill", start, days, sleep)
                sleep(PAUSE_SECONDS)
    if failed:
        return 1  # replacing NRT while SP has a hole would lose data: fix and re-run first
    if not ranges:
        return 0
    for code, (low, high) in ranges.items():
        client.rpc("replace_nrt_with_sp", {
            "nrt_code": code.replace("_SP", "_NRT"), "d_from": low.isoformat(), "d_to": high.isoformat(),
        }).execute()
    client.rpc("refresh_excluded_cells", {}).execute()
    rebuild_by_year(client, min(low for low, _ in ranges.values()), max(high for _, high in ranges.values()))
    client.rpc("recompute_calibration", {}).execute()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.ingest")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("daily", help="load the latest NRT days (run once a day)")
    backfill_parser = commands.add_parser("backfill", help="load the SP archive (idempotent, resumable)")
    backfill_parser.add_argument("--source", help="only this product, e.g. MODIS_SP")
    backfill_parser.add_argument("--from", dest="date_from", type=date.fromisoformat, help="YYYY-MM-DD")
    backfill_parser.add_argument("--to", dest="date_to", type=date.fromisoformat, help="YYYY-MM-DD")
    args = parser.parse_args(argv)

    logging.basicConfig(level=LOG_LEVEL)
    for name in ("httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)  # they log request URLs, which contain the key
    if not SUPABASE_INGEST_KEY:
        print("SUPABASE_INGEST_KEY is not set; the ingest needs the write key.")
        return 2

    client = create_supabase_client(SUPABASE_URL, SUPABASE_INGEST_KEY)
    with httpx.Client(timeout=60) as http:
        try:
            if args.command == "daily":
                return daily(client, http, datetime.now(UTC).date())
            return backfill(client, http, args.source, args.date_from, args.date_to)
        except FirmsResponseError as exc:
            print(f"FIRMS did not return data: {_describe(exc)}")
            return 2
