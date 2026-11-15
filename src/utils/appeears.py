"""Client helpers for the NASA AppEEARS API (https://appeears.earthdatacloud.nasa.gov/api/)."""

import netrc
from concurrent.futures import ThreadPoolExecutor
import time
from datetime import datetime
from pathlib import Path

import requests

API_URL = "https://appeears.earthdatacloud.nasa.gov/api"
EARTHDATA_HOST = "urs.earthdata.nasa.gov"

BBox = tuple[float, float, float, float]  # (west, south, east, north)


def credentials_from_netrc(host: str = EARTHDATA_HOST, netrc_path: str | None = None) -> tuple[str, str]:
    """Return the (username, password) stored for the Earthdata Login host in a .netrc file."""
    entry = netrc.netrc(netrc_path).authenticators(host)
    if entry is None:
        raise LookupError(f"No credentials for {host} in .netrc")
    username, _, password = entry
    if not username or not password:
        raise LookupError(f"Incomplete credentials for {host} in .netrc")
    return username, password


def login(username: str, password: str, timeout: float = 60) -> dict[str, str]:
    """Log in with Earthdata Login credentials and return bearer-token request headers."""
    response = requests.post(f"{API_URL}/login", auth=(username, password), timeout=timeout)
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['token']}"}


def logout(headers: dict[str, str], timeout: float = 60) -> None:
    """Invalidate the bearer token."""
    requests.post(f"{API_URL}/logout", headers=headers, timeout=timeout)


def bbox_to_geojson(bbox: BBox) -> dict:
    """Convert a (west, south, east, north) bounding box into a GeoJSON FeatureCollection polygon."""
    west, south, east, north = bbox
    if west >= east or south >= north:
        raise ValueError(f"Invalid bounding box {bbox}: expected west < east and south < north")
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
            },
        }],
    }


def build_area_task(
    name: str,
    bbox: BBox,
    start: datetime,
    stop: datetime,
    layers: list[dict[str, str]],
    output_format: str = "netcdf4",
    projection: str = "geographic",
    geo: dict | None = None,
) -> dict:
    """Build the JSON body of an AppEEARS area task.

    geo: GeoJSON FeatureCollection of the area (e.g. a region polygon); defaults to the `bbox` rectangle.
    AppEEARS sets pixels outside the polygon to fill values.
    """
    if stop < start:
        raise ValueError("stop must not be before start")
    if not layers:
        raise ValueError("At least one layer is required")
    return {
        "task_type": "area",
        "task_name": name,
        "params": {
            "dates": [{"startDate": f"{start:%m-%d-%Y}", "endDate": f"{stop:%m-%d-%Y}"}],
            "layers": layers,
            "geo": geo if geo is not None else bbox_to_geojson(bbox),
            "output": {"format": {"type": output_format}, "projection": projection},
        },
    }


def submit_task(headers: dict[str, str], task: dict, timeout: float = 60) -> str:
    """Submit a task and return its task ID."""
    response = requests.post(f"{API_URL}/task", json=task, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()["task_id"]


def wait_for_task(
    headers: dict[str, str],
    task_id: str,
    poll_seconds: float = 30,
    max_wait_seconds: float | None = None,
    timeout: float = 60,
) -> None:
    """Block until the task is done; raise if it fails or exceeds max_wait_seconds."""
    started = time.monotonic()
    while True:
        response = requests.get(f"{API_URL}/task/{task_id}", headers=headers, timeout=timeout)
        response.raise_for_status()
        status = response.json()["status"]
        print(f"{datetime.now():%H:%M:%S} task {task_id}: {status}")
        if status == "done":
            return
        if status == "error":
            raise RuntimeError(f"AppEEARS task {task_id} failed")
        if max_wait_seconds is not None and time.monotonic() - started > max_wait_seconds:
            raise TimeoutError(f"AppEEARS task {task_id} not done after {max_wait_seconds} s")
        time.sleep(poll_seconds)


def download_bundle(
    headers: dict[str, str],
    task_id: str,
    directory: str | Path,
    extensions: tuple[str, ...] = (".nc",),
    timeout: float = 300,
    name_contains: tuple[str, ...] | None = None,
    workers: int = 1,
    retries: int = 3,
    retry_wait_seconds: float = 5,
) -> list[Path]:
    """Download the files of a finished task whose names end with one of `extensions`.

    name_contains: keep only files whose name contains one of these strings (e.g. layer names).
    workers: parallel downloads (useful for bundles of many small GeoTIFFs).
    retries: attempts per file after a connection error or HTTP error, waiting retry_wait_seconds x attempt.
    Files already in `directory` with the bundle's size are not downloaded again.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    response = requests.get(f"{API_URL}/bundle/{task_id}", headers=headers, timeout=timeout)
    response.raise_for_status()

    files = [
        f for f in response.json()["files"]
        if f["file_name"].endswith(extensions)
        and (name_contains is None or any(part in f["file_name"] for part in name_contains))
    ]

    def fetch(f: dict) -> Path:
        path = directory / Path(f["file_name"]).name
        if path.exists() and "file_size" in f and path.stat().st_size == f["file_size"]:
            return path
        partial = path.with_name(path.name + ".part")
        for attempt in range(retries + 1):
            try:
                with requests.get(
                    f"{API_URL}/bundle/{task_id}/{f['file_id']}", headers=headers, stream=True, timeout=timeout
                ) as download:
                    download.raise_for_status()
                    with partial.open("wb") as out:
                        for chunk in download.iter_content(chunk_size=8 * 1024 * 1024):
                            out.write(chunk)
                partial.replace(path)
                return path
            except (requests.RequestException, RuntimeError):
                if attempt == retries:
                    raise
                time.sleep(retry_wait_seconds * (attempt + 1))
        return path

    if workers <= 1:
        return [fetch(f) for f in files]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fetch, files))
