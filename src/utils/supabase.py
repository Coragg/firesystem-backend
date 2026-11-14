from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from supabase import Client, create_client


def create_supabase_client(url: str, key: str) -> Client:
    """Build a Supabase client for the given project URL and publishable key."""
    return create_client(url, key)


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    """Return the shared Supabase client configured from environment variables."""
    from src.config import SUPABASE_KEY, SUPABASE_URL

    return create_supabase_client(SUPABASE_URL, SUPABASE_KEY)


SupabaseDep = Annotated[Client, Depends(get_supabase)]
