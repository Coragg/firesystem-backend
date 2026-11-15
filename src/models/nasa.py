"""[AI] Models of the NASA FIRMS pass-through endpoints (`/api/v1/fire` and `/api/v1/nasa/*`)."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# [AI] The FIRMS active-fire products this API accepts. SP = standard processing (final archive),
# NRT = near real time (provisional). The ingest uses this list as its allow-list too.
FirmsSource = Literal[
    "MODIS_NRT",
    "MODIS_SP",
    "VIIRS_NOAA20_NRT",
    "VIIRS_NOAA20_SP",
    "VIIRS_NOAA21_NRT",
    "VIIRS_NOAA21_SP",
    "VIIRS_SNPP_NRT",
    "VIIRS_SNPP_SP",
]


class FireDetection(BaseModel):
    """[AI] One FIRMS CSV row. Only common columns are typed; MODIS and VIIRS columns differ."""

    # [AI] extra="allow" keeps the sensor-specific columns (bright_ti4, scan, ...) in the response.
    model_config = ConfigDict(extra="allow")

    latitude: float | None = Field(default=None, description="Detection latitude in decimal degrees, when supplied by FIRMS.")
    longitude: float | None = Field(default=None, description="Detection longitude in decimal degrees, when supplied by FIRMS.")
    acq_date: str | None = Field(default=None, description="Satellite acquisition date, when supplied by FIRMS.")
    acq_time: str | None = Field(default=None, description="Satellite acquisition time, when supplied by FIRMS.")
    confidence: str | None = Field(default=None, description="Sensor-specific detection confidence, when supplied by FIRMS.")
    frp: float | None = Field(default=None, description="Fire radiative power in MW, when supplied by FIRMS.")


class FireQueryResult(BaseModel):
    """[AI] Query echoed back together with the detections FIRMS returned."""

    source: FirmsSource = Field(description="FIRMS MODIS or VIIRS active fire product ID used for the query.")
    area: str = Field(description="Queried area: world or west,south,east,north bounding box.")
    days: int = Field(description="Requested number of days (1–5).")
    detections: list[FireDetection] = Field(description="Active fire detections returned by FIRMS.")


class FirmsSourceAvailability(BaseModel):
    """[AI] One row of FIRMS' data_availability service."""

    data_id: str = Field(description="FIRMS dataset identifier; does not indicate a detection or count.")
    min_date: str | None = Field(default=None, description="Earliest available dataset date, in GMT.")
    max_date: str | None = Field(default=None, description="Latest available dataset date, in GMT.")
    extra: dict[str, Any] = Field(default_factory=dict, description="Additional columns returned by FIRMS, if any.")


class FirmsQuota(BaseModel):
    """[AI] Usage of the FIRMS map key in its current quota window."""

    transaction_limit: int = Field(description="Maximum transactions allowed in the current quota interval.")
    current_transactions: int = Field(description="Transactions used in the current interval.")
    transaction_interval: str = Field(description="Quota interval reported by FIRMS, for example 10 minutes.")


class FirmsHealth(BaseModel):
    """[AI] Whether FIRMS can be reached and whether the configured map key works."""

    available: bool = Field(description="Whether the FIRMS status service responded.")
    key_valid: bool = Field(description="Whether the configured map key was considered valid by the status check.")
    quota: FirmsQuota | None = Field(default=None, description="Quota information when FIRMS returned it.")
