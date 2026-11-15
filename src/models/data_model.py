"""[AI] Models of the `/api/v1/data-model/*` endpoints: the inputs of the hotspot model (AppEEARS-derived values)."""

from pydantic import BaseModel


class LaiPoint(BaseModel):
    """[AI] Mean leaf area index (m²/m²) of a 500 m pixel in one month; `lai` is None when no good composite exists."""

    month: str  # [AI] "YYYY-MM"
    lai: float | None


class Terrain(BaseModel):
    """[AI] SRTM elevation (m) and slope (degrees) averaged over a 500 m pixel."""

    elevation_m: float
    slope_deg: float


class LandCover(BaseModel):
    """[AI] IGBP land cover class (MCD12Q1 LC_Type1) of the latest year <= `max_year`; both None if there is none."""

    year: int | None
    igbp_class: int | None
