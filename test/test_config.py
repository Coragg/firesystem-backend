from src.config import parse_origins


def test_parse_origins_default_when_unset():
    assert parse_origins(None) == ["http://localhost:5173"]


def test_parse_origins_splits_and_trims():
    assert parse_origins("http://a.test, http://b.test") == ["http://a.test", "http://b.test"]
