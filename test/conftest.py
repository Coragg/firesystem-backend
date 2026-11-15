import pytest

from src.main import app
from src.utils.supabase import get_supabase


@pytest.fixture
def use_fake_db():
    """Install a FakeClient as the API's Supabase client for one test."""

    def install(fake) -> None:
        app.dependency_overrides[get_supabase] = lambda: fake

    yield install
    app.dependency_overrides.clear()
