"""[AI] `GET /api/v1/nasa/quota`: transaction usage of the configured FIRMS map key."""

import httpx
from fastapi import APIRouter, HTTPException

from src.models.nasa import FirmsQuota
from src.models.response import ApiResponse
from src.utils.firms import fetch_quota

router = APIRouter(prefix="/api/v1/nasa", tags=["NASA FIRMS"])


@router.get(
    "/quota",
    response_model=ApiResponse[FirmsQuota],
    summary="Get NASA FIRMS quota",
    description="Returns transaction limit, current usage, and the transaction interval for the configured map key.",
)
def get_quota() -> ApiResponse[FirmsQuota]:
    """[AI] Proxy the FIRMS key-status service.

    [AI] Upstream failures map to 502 Bad Gateway: the request was fine, the dependency was not.
    """
    try:
        payload = fetch_quota()
        return ApiResponse(data=FirmsQuota.model_validate(payload))
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="NASA FIRMS request failed") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="NASA FIRMS returned invalid quota data") from exc
