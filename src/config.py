"""[AI] Application settings, read once from environment variables (and `.env`) at import time.

[AI] Decision visible in the code: required settings use `os.environ[...]`, so a missing value fails
fast with a KeyError when the app starts instead of surfacing later inside a request; optional
settings use `os.getenv(...)` with a default.
"""

import os

from dotenv import load_dotenv

# [AI] Values already set in the real environment win over `.env` (python-dotenv's default).
load_dotenv()


def parse_origins(raw: str | None) -> list[str]:
    """Split a comma-separated CORS origin list; default to the local frontend."""
    if not raw:
        return ["http://localhost:5173"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


# NASA api
FIRMS_MAP_KEY = os.environ["FIRMS_MAP_KEY"]
FIRMS_BASE_URL = os.environ["FIRMS_BASE_URL"]

# SUPABASE (API reads with SUPABASE_KEY; only the ingest script uses SUPABASE_INGEST_KEY)
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
SUPABASE_INGEST_KEY = os.getenv("SUPABASE_INGEST_KEY")

# API
CORS_ORIGINS = parse_origins(os.getenv("CORS_ORIGINS"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# jwt
SECRET_KEY = os.environ["SECRET_KEY"]
ALGORITHM = os.environ["ALGORITHM"]
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))

# Hotspot forecast model (saved by src/notebooks/XGBoost_Hotspot_Forecast.ipynb)
HOTSPOT_MODEL_DIR = os.getenv("HOTSPOT_MODEL_DIR", "data/models/hotspots/Maule")
# Where the model's input data comes from: "api" (APPEEARS_DATA_URL endpoints) or "local" (downloaded files)
HOTSPOT_FEATURE_SOURCE = os.getenv("HOTSPOT_FEATURE_SOURCE", "api")
APPEEARS_DATA_URL = os.getenv("APPEEARS_DATA_URL", "")
APPEEARS_LOCAL_DIR = os.getenv("APPEEARS_LOCAL_DIR", "data/AppEEARS/Maule")
