"""[AI] Supabase client creation and the FastAPI dependency that injects it into route handlers."""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from supabase import Client, create_client


def create_supabase_client(url: str, key: str) -> Client:
    """Build a Supabase client for the given project URL and publishable key."""
    return create_client(url, key)


# [AI] lru_cache(maxsize=1) makes this a lazy singleton: one client for the whole process.
@lru_cache(maxsize=1)
def get_supabase() -> Client:
    """Return the shared Supabase client configured from environment variables."""
    # [AI] Deferred import: config is only read when the API first asks for a client. The ingest
    # does not use this function; it builds its own client with the write key via `create_supabase_client`.
    from src.config import SUPABASE_KEY, SUPABASE_URL

    return create_supabase_client(SUPABASE_URL, SUPABASE_KEY)


# [AI] Use as a route parameter type (`client: SupabaseDep`); tests can replace it via dependency_overrides.
SupabaseDep = Annotated[Client, Depends(get_supabase)]
