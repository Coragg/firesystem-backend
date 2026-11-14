import os

from dotenv import load_dotenv

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

# jwt (optional until login exists)
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))
