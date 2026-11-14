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
uv run fastapi dev
```

The API is available at <http://127.0.0.1:5000>. The root endpoint (`GET /`) returns a welcome response. Interactive API documentation is available at <http://127.0.0.1:5000/docs>, with the OpenAPI schema at <http://127.0.0.1:5000/openapi.json>.

To run the server without development reload:

```bash
uv run fastapi run
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
