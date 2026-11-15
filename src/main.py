import logging
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from src.config import CORS_ORIGINS, LOG_LEVEL
from src.models.response import ApiResponse
from src.routers.api_database import calendar as db_calendar
from src.routers.api_database import health as db_health
from src.routers.api_database import hotspots as db_hotspots
from src.routers.api_database import sources as db_sources
from src.routers.api_model import hotspot
from src.routers.api_nasa import fires, quota
from src.routers.api_nasa import health as nasa_health
from src.routers.api_nasa import sources as nasa_sources
from src.utils.pyproject_variables import name, version, description

logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("firesystem")
# These log every request URL at INFO, and FIRMS puts the map key in the URL.
for _noisy in ("httpx", "httpx2", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# [AI] main.py only holds app-wide infrastructure (middleware, CORS, error handler, root). Every endpoint
# lives in src/routers/: api_database (reads the data loaded by the ingest job in src/ingest/),
# api_nasa (live NASA FIRMS pass-through) and api_model (hotspot forecast).
app = FastAPI()
app.include_router(db_health.router)
app.include_router(db_hotspots.router)
app.include_router(db_sources.router)
app.include_router(db_calendar.router)
app.include_router(nasa_health.router)
app.include_router(quota.router)
app.include_router(nasa_sources.router)
app.include_router(fires.router)
app.include_router(hotspot.router)

# Added first = innermost. request_log is declared last so it wraps everything.
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],  # [AI] lets the browser app read the id to quote in bug reports
)


@app.middleware("http")
async def request_log(request: Request, call_next):
    """Tag each request with an id, return it in X-Request-ID and log its duration."""
    request.state.request_id = request_id = uuid4().hex[:12]
    start = perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        logger.info(
            "%s %s %s %s %.0fms", request_id, request.method, request.url.path, status,
            (perf_counter() - start) * 1000,
        )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: log the traceback, answer in the same {"detail": ...} shape as every other error."""
    request_id = getattr(request.state, "request_id", None)
    logger.exception("unhandled error id=%s", request_id)
    headers = {"X-Request-ID": request_id} if request_id else None
    return JSONResponse(status_code=500, content={"detail": "Internal server error"}, headers=headers)


@app.get("/")
async def root() -> ApiResponse[dict]:
    """[AI] Welcome message with the name, version and description from pyproject.toml."""
    return ApiResponse(
        data={
            "message": f"Welcome to {name}",
            "version": version,
            "description": description,
        }
    )
