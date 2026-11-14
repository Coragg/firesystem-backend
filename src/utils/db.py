import logging
from datetime import date, datetime

import httpx
from fastapi import HTTPException
from postgrest.exceptions import APIError
from supabase import Client

from src.utils.harmonize import CountRow

logger = logging.getLogger("firesystem")


def run(query) -> list[dict]:
    """Execute a Supabase query; the one place that turns database failures into API errors."""
    try:
        return query.execute().data
    except httpx.TransportError as exc:
        raise HTTPException(
            status_code=503, detail="The data store is not responding. Try again in a few minutes."
        ) from exc
    except APIError as exc:
        logger.error("supabase query failed code=%s", exc.code)
        raise


def calendar_counts(client: Client, aoi: dict, d_from: date, d_to: date, mtd_day: int) -> list[CountRow]:
    """Reference-satellite cell-days per year, month and reference for an area."""
    return run(client.rpc("calendar_counts", {
        "aoi": aoi, "d_from": d_from.isoformat(), "d_to": d_to.isoformat(), "mtd_day": mtd_day,
    }))


def calibration(client: Client) -> dict[tuple[str, int], float]:
    """Scale factors to S-NPP, keyed by (reference, month)."""
    rows = run(client.table("calibration_factor").select("reference,month,k"))
    return {(r["reference"], r["month"]): r["k"] for r in rows}


def select(client: Client, table: str, columns: str = "*") -> list[dict]:
    return run(client.table(table).select(columns))


def rpc(client: Client, name: str, params: dict | None = None) -> list[dict]:
    return run(client.rpc(name, params or {}))


def hotspots_page(
    client: Client, aoi: dict, d_from: date, d_to: date, after_id: int | None, limit: int
) -> list[dict]:
    """Detections in an area, ordered by id, starting after the given cursor."""
    return rpc(client, "hotspots_page", {
        "aoi": aoi, "d_from": d_from.isoformat(), "d_to": d_to.isoformat(),
        "after_id": after_id, "page_size": limit,
    })


def hotspots_count(client: Client, aoi: dict, d_from: date, d_to: date) -> int:
    """How many detections an export of this area and range would contain."""
    return int(rpc(client, "hotspots_count", {"aoi": aoi, "d_from": d_from.isoformat(), "d_to": d_to.isoformat()}))


def last_ingest_at(client: Client) -> datetime | None:
    """When the last successful ingest batch finished, or None if there is none."""
    rows = run(
        client.table("ingest_batch")
        .select("finished_at")
        .eq("status", "ok")
        .order("finished_at", desc=True)
        .limit(1)
    )
    return datetime.fromisoformat(rows[0]["finished_at"]) if rows else None
