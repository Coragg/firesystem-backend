import httpx
from fastapi import APIRouter, HTTPException

from src.models.nasa import FirmsSourceAvailability
from src.models.response import ApiResponse
from src.utils.firms import fetch_sources

router = APIRouter(prefix="/api/v1/nasa", tags=["NASA FIRMS"])


@router.get(
    "/sources",
    response_model=ApiResponse[list[FirmsSourceAvailability]],
    summary="List NASA FIRMS sources",
    description=(
        "Returns FIRMS dataset IDs and their currently available date ranges. These are availability "
        "windows, not detections or counts. The list includes products that the active-fire endpoint "
        "does not currently accept, such as burned-area products. Dates are reported in GMT."
    ),
)
def get_sources() -> ApiResponse[list[FirmsSourceAvailability]]:
    try:
        rows = fetch_sources()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="NASA FIRMS request failed") from exc
    return ApiResponse(data=[FirmsSourceAvailability(
        data_id=row.get("data_id", ""),
        min_date=row.get("min_date"),
        max_date=row.get("max_date"),
        extra={key: value for key, value in row.items() if key not in {"data_id", "min_date", "max_date"}},
    ) for row in rows])
