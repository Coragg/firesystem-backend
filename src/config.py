"""[AI] Application settings, read once from environment variables (and `.env`) at import time.

[AI] Decision visible in the code: required settings use `os.environ[...]`, so a missing value fails
fast with a KeyError when the app starts instead of surfacing later inside a request; optional
settings use `os.getenv(...)` with a default.
"""

import os
from pathlib import Path

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
# [AI] Data older than 36 h counts as stale: the ingest runs daily, so this allows one late or missed run.
STALE_AFTER_HOURS = 36

# jwt
SECRET_KEY = os.environ["SECRET_KEY"]
ALGORITHM = os.environ["ALGORITHM"]
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))

# Hotspot forecast model (saved by src/notebooks/XGBoost_Hotspot_Forecast.ipynb)
# [AI] model.json + metadata.json live in models/ at the repository root. The default is resolved from this
# file's location, so it works whatever folder the server is started from.
REPO_ROOT = Path(__file__).resolve().parent.parent
HOTSPOT_MODEL_DIR = os.getenv("HOTSPOT_MODEL_DIR", str(REPO_ROOT / "models"))
# [AI] The model always gets its input data from the data-model API over HTTP. APPEEARS_DATA_URL is that
# API's base URL (e.g. https://<host>/api/v1/data-model); empty means this same server's /api/v1/data-model.
APPEEARS_DATA_URL = os.getenv("APPEEARS_DATA_URL", "")
# [AI] AppEEARS files read by the data-model API only (src/routers/api_data_model), never by the model.
APPEEARS_LOCAL_DIR = os.getenv("APPEEARS_LOCAL_DIR", str(REPO_ROOT / "data" / "AppEEARS" / "Maule"))
