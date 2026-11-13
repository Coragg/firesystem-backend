from typing import Literal

from pydantic import BaseModel


class ApiResponse[DataType](BaseModel):
    success: Literal[True] = True
    data: DataType | None = None
