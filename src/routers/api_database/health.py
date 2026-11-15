"""[AI] `GET /api/v1/health`: whether the API and database answer, and how fresh the loaded data is."""

from datetime import UTC, datetime

from fastapi import APIRouter

from src.config import STALE_AFTER_HOURS
from src.models.response import ApiResponse
from src.models.source import HealthData
from src.utils import db
from src.utils.supabase import SupabaseDep

router = APIRouter(prefix="/api/v1", tags=["Database"])


@router.get("/health")
def health_view(client: SupabaseDep) -> ApiResponse[HealthData]:
    """Report that the API and database answer, and how fresh the loaded data is."""
    last = db.last_ingest_at(client)
    hours = None if last is None else (datetime.now(UTC) - last).total_seconds() / 3600
    status = "empty" if hours is None else "stale" if hours > STALE_AFTER_HOURS else "fresh"
    return ApiResponse(
        data=HealthData(
            api="ok",
            database="ok",
            data_status=status,
            last_ingest_at=last,
            hours_since_last_ingest=None if hours is None else round(hours, 1),
        )
    )
