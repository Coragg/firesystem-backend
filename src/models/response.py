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
