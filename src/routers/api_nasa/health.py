import httpx
from fastapi import APIRouter

from src.models.nasa import FirmsHealth, FirmsQuota
from src.models.response import ApiResponse
from src.utils.firms import fetch_quota

router = APIRouter(prefix="/api/v1/nasa", tags=["NASA FIRMS"])


@router.get(
    "/health",
    response_model=ApiResponse[FirmsHealth],
    summary="Check NASA FIRMS health",
    description="Checks FIRMS connectivity and the configured map key using its quota status service.",
)
def get_health() -> ApiResponse[FirmsHealth]:
    try:
        quota = FirmsQuota.model_validate(fetch_quota())
        return ApiResponse(data=FirmsHealth(available=True, key_valid=True, quota=quota))
    except httpx.HTTPStatusError as exc:
        key_valid = exc.response.status_code != 401
        return ApiResponse(data=FirmsHealth(available=True, key_valid=key_valid))
    except (httpx.HTTPError, ValueError, TypeError):
        return ApiResponse(data=FirmsHealth(available=False, key_valid=False))
