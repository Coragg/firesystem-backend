"""[AI] Geographic coverage of the loaded data: the Chilean areas the ingest downloads from FIRMS."""

BBox = tuple[float, float, float, float]  # west, south, east, north

# Areas the ingest downloads; the API warns when a request falls outside them.
AREAS: dict[str, BBox] = {
    "mainland": (-75.8, -56.1, -66.4, -17.4),
    "easter_island": (-109.5, -27.3, -109.2, -27.0),
    "juan_fernandez": (-80.9, -33.8, -78.7, -33.6),
}


def intersects_coverage(bbox: BBox) -> bool:
    """True when the bounding box touches any area we have data for."""
    west, south, east, north = bbox
    # [AI] Two boxes overlap unless one lies entirely to one side of the other; edges that touch count.
    return any(
        west <= a_east and east >= a_west and south <= a_north and north >= a_south
        for a_west, a_south, a_east, a_north in AREAS.values()
    )
