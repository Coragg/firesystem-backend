"""[AI] Envelope that every successful API response is wrapped in.

[AI] Decision visible in the code: success responses are `{"success": true, "data": ..., "meta": ...}`;
errors are not wrapped and keep FastAPI's `{"detail": ...}` shape (see the handler in `src/main.py`).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Meta(BaseModel):
    """[AI] Context about the data: when it was last loaded, the method version, and warning codes.

    [AI] Warnings are short machine-readable codes (e.g. DATA_STALE, RANGE_CLIPPED, CALIBRATION_MISSING)
    so the frontend can decide how to show them.
    """

    data_updated_at: datetime | None = None
    method_version: str = "v1"
    warnings: list[str] = Field(default_factory=list)


class ApiResponse[DataType](BaseModel):
    """[AI] Generic success envelope; `DataType` is the payload model of each endpoint."""

    # [AI] Always True: failures are returned as HTTP errors, never as success=False.
    success: Literal[True] = True
    data: DataType | None = None
    meta: Meta | None = None
