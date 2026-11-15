# FireSystem Backend

A FastAPI backend project managed with [uv](https://docs.astral.sh/uv/).

## Requirements

- Python 3.14 or newer
- `uv`

Install `uv` by following the [official installation guide](https://docs.astral.sh/uv/getting-started/installation/).

## Getting started

Clone the repository and move into the project directory, then install the project and its dependencies:

```bash
git clone <repository-url>
cd firesystem-backend
uv sync
```

`uv sync` creates the project environment (in `.venv`) and installs the dependencies recorded in `uv.lock`.

## Run the API

Start the development server with automatic reload:

```bash
uv run fastapi dev --port 5000
```

The API is available at <http://127.0.0.1:5000>. The root endpoint (`GET /`) returns a welcome response. Interactive API documentation is available at <http://127.0.0.1:5000/docs>, with the OpenAPI schema at <http://127.0.0.1:5000/openapi.json>.

To run the server without development reload:

```bash
uv run fastapi run --port 5000
```

The FastAPI entry point is configured in `pyproject.toml` as `src.main:app`.

## Run tests

The project uses [pytest](https://docs.pytest.org/) for tests. Run the test suite from the project root with:

```bash
uv run pytest
```

Tests are stored in the `test/` directory.

## Add dependencies

Add a runtime dependency with:

```bash
uv add <package-name>
```

Add a development-only dependency with:

```bash
uv add --dev <package-name>
```

Both commands update `pyproject.toml` and `uv.lock`. After pulling project changes, run `uv sync` to bring your environment up to date.

## Load the data (ingest)

The API only reads. The team loads NASA FIRMS data into Supabase with a separate script, using a write key that must never be on the API host.

1. Run `db/migrations/001_initial_schema.sql` (tables) and then `db/migrations/002_functions_and_permissions.sql` (PostGIS, functions, permissions) in the Supabase SQL editor. Run each file once.
2. Put these variables in `.env` (names only are listed in `.env.example`): `FIRMS_MAP_KEY`, `FIRMS_BASE_URL`, `SUPABASE_URL` and `SUPABASE_INGEST_KEY` (the service key, because tables only allow reads to the public key).
3. Load data:

```bash
# Historical archive (SP). Idempotent: chunks already loaded are skipped, so you can re-run after a failure.
uv run python -m src.ingest backfill
uv run python -m src.ingest backfill --source MODIS_SP --from 2024-01-01 --to 2024-01-10   # small test

# Latest days (NRT). Run once a day.
uv run python -m src.ingest daily
```

Exit codes: `0` ok, `1` some chunks failed (re-run), `2` FIRMS rejected the request or the write key is missing.

To run `daily` every day on Windows, create a Task Scheduler task that runs `uv run python -m src.ingest daily` in this folder. Check it with `GET /api/v1/health`: `data_status` is `fresh` when the last load finished less than 36 hours ago.
