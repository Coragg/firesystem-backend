import os
from pathlib import Path

import pytest

from src import config
from src.config import parse_origins


@pytest.mark.skipif("HOTSPOT_MODEL_DIR" in os.environ, reason="an explicit HOTSPOT_MODEL_DIR overrides the default")
def test_model_dir_defaults_to_repo_models_folder():
    """[AI] Without HOTSPOT_MODEL_DIR the model is loaded from models/ at the repository root."""
    repo_root = Path(__file__).resolve().parent.parent
    assert Path(config.HOTSPOT_MODEL_DIR) == repo_root / "models"
    assert (Path(config.HOTSPOT_MODEL_DIR) / "model.json").exists()
    assert (Path(config.HOTSPOT_MODEL_DIR) / "metadata.json").exists()


def test_parse_origins_default_when_unset():
    assert parse_origins(None) == ["http://localhost:5173"]


def test_parse_origins_splits_and_trims():
    assert parse_origins("http://a.test, http://b.test") == ["http://a.test", "http://b.test"]
