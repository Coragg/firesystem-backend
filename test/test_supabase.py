from supabase import Client

from src.utils.supabase import create_supabase_client


def test_create_supabase_client_returns_client():
    client = create_supabase_client(
        "https://example.supabase.co", "sb_publishable_test_key"
    )

    assert isinstance(client, Client)
