from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Meta(BaseModel):
    data_updated_at: datetime | None = None
    method_version: str = "v1"
    warnings: list[str] = Field(default_factory=list)


class ApiResponse[DataType](BaseModel):
    success: Literal[True] = True
    data: DataType | None = None
    meta: Meta | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict] | None = None
    request_id: str | None = None


class ApiError(BaseModel):
    success: Literal[False] = False
    error: ErrorBody
