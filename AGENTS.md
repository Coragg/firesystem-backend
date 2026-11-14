# Project Overview

FireSystem Backend is the API for a project created for NASA Space Apps Challenge 2026's [Harmonization of MODIS and VIIRS Hot Spots](https://www.spaceappschallenge.org/2026/challenges/modis-and-viirs-hot-spots/) challenge. The project aims to harmonize active fire hotspot records from the MODIS and VIIRS satellite sensors into a burning activity calendar, helping users explore historical fire patterns and identify unusual conditions and critical periods for a selected area. This repository contains the Python **FastAPI** backend that supports that web application and is managed with `uv`.

## Tech Stack

- **Language:** Python 3.14+
- **Framework:** FastAPI
- **Package manager and environment:** `uv`
- **Testing:** `pytest` (with FastAPI's `TestClient`)
- **Configuration:** Environment variables loaded from `.env` with `python-dotenv`

## Folder Structure

```text
.
├── api/firesystem/     # Bruno API collection and request definitions
├── src/
│   ├── main.py         # FastAPI application and route handlers
│   ├── config.py       # Environment/configuration setup
│   ├── models/         # Pydantic API models
│   └── utils/          # Shared utilities and project metadata helpers
├── test/               # Pytest test suite
├── pyproject.toml      # Project metadata, dependencies, and tool config
├── uv.lock             # Locked dependency versions
└── README.md           # Setup and usage documentation
```

## Dev Commands

Run these from the repository root:

```bash
# Install/synchronize dependencies and the project environment
uv sync

# Start the API with reload for local development
uv run fastapi dev

# Run the API without development reload
uv run fastapi run

# Run the test suite
uv run pytest

# Add a runtime dependency
uv add <package-name>

# Add a development dependency
uv add --dev <package-name>
```

The FastAPI entry point is `src.main:app`. The development server defaults to `http://127.0.0.1:8000`; interactive docs are at `/docs`.

## Code Conventions

- Follow PEP 8 and keep code clear, focused, and maintainable.
- Use type annotations for function parameters and return values.
- Define request/response data models with Pydantic where appropriate.
- Keep route handlers in `src/main.py`; move reusable models and helpers into their respective `src/models/` and `src/utils/` modules.
- Add or update unit tests for every function introduced or changed by a task. Put tests in `test/` and use `pytest`.
- Keep dependencies declared through `uv` so `pyproject.toml` and `uv.lock` stay in sync.
- Read configuration from environment variables; do not commit secrets or real credentials.

## Git Rules

- Review `git status` and the relevant diff before making or summarizing repository changes.
- Preserve unrelated user changes; do not discard or overwrite them.
- Do not run `git push`.
- Do not run `git commit` unless the user confirms.
- Do not rewrite Git history or run destructive Git commands unless the user explicitly requests it.

## Agent Rules

- **Plan format:** When asked for a plan, ALWAYS follow the structure defined in `.agent/rules/plan_structure.md`.
- **Bruno collections:** Before creating, editing, or troubleshooting files under `api/firesystem/`, read `.agent/skills/bruno-collections/SKILL.md`. Keep its copies in `.claude/skills/bruno-collections/SKILL.md` and `.codex/skills/bruno-collections/SKILL.md` synchronized.

## Additional Context

- `pyproject.toml` configures the FastAPI entry point and pytest's project-root import path.
- `src/utils/pyproject_variables.py` reads the package name, version, and description from `pyproject.toml`.
- The Bruno collection in `api/firesystem/` documents the API requests.
- Keep this file aligned with the repository as its structure and development workflow evolve.
