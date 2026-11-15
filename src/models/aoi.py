"""[AI] Area of interest (AOI) sent by the frontend: a GeoJSON Polygon, validated before it reaches PostGIS."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

# [AI] Caps on polygon size, which keep the database queries (`aoi_area` / `st_intersects`) cheap.
MAX_VERTICES = 1000
MAX_RINGS = 10
# [AI] Appended to coordinate errors because swapped lat/lon is the most likely cause.
SWAP_HINT = "GeoJSON uses [lon, lat]"


class PolygonAoi(BaseModel):
    """Area of interest drawn by the user, as a GeoJSON Polygon."""

    # [AI] Unknown keys are rejected so a typo (or a Feature wrapper) fails loudly instead of being ignored.
    model_config = ConfigDict(extra="forbid")

    type: Literal["Polygon"]
    coordinates: list[list[tuple[float, float]]]  # [AI] rings of (lon, lat); the first ring is the outer one

    @model_validator(mode="after")
    def _validate_rings(self) -> "PolygonAoi":
        """[AI] Check size limits and coordinate ranges, and close any ring that is left open."""
        if not 1 <= len(self.coordinates) <= MAX_RINGS:
            raise ValueError(f"a polygon needs between 1 and {MAX_RINGS} rings")
        if sum(len(ring) for ring in self.coordinates) > MAX_VERTICES:
            raise ValueError(f"a polygon can have at most {MAX_VERTICES} vertices")

        closed_rings = []
        for ring in self.coordinates:
            points = list(ring)
            # [AI] GeoJSON requires closed rings; the API closes them for the client instead of rejecting.
            if points and points[0] != points[-1]:
                points.append(points[0])
            if len(set(points)) < 3:
                raise ValueError("each ring needs at least 3 distinct points")
            for lon, lat in points:
                if not -90 <= lat <= 90:
                    raise ValueError(f"latitude {lat} is out of range (-90 to 90); {SWAP_HINT}")
                if not -180 <= lon <= 180:
                    raise ValueError(f"longitude {lon} is out of range (-180 to 180); {SWAP_HINT}")
            closed_rings.append(points)

        self.coordinates = closed_rings
        return self

    def bbox(self) -> tuple[float, float, float, float]:
        """Bounding box of the outer ring as (west, south, east, north)."""
        lons = [lon for lon, _ in self.coordinates[0]]
        lats = [lat for _, lat in self.coordinates[0]]
        return (min(lons), min(lats), max(lons), max(lats))
