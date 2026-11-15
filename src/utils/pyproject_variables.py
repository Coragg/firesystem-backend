"""[AI] Project name, version and description read from `pyproject.toml`, so they are defined in one place."""

import tomllib
from pathlib import Path

# [AI] src/utils/ -> src/ -> repository root
pyproject_path = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
with pyproject_path.open("rb") as toml_file:
    pyproject_data = tomllib.load(toml_file)

name: str = pyproject_data["project"]["name"]
version: str = pyproject_data["project"]["version"]
description: str = pyproject_data["project"]["description"]
