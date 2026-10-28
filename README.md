# chevlab-backend

Backend API for a homelab monitoring dashboard. Collects system metrics from the host machine and serves them to a web frontend.

> **Status:** early development — project is initialized, features are not implemented yet.

## Planned features

- **Live metrics** — CPU, RAM, disk, temperature, and uptime, auto-refreshing every few seconds.
- **History graphs** — 1-hour / 24-hour views, backed by a background job that samples metrics into the database.
- **Online/offline status** — with a dedicated offline page.
- **Authentication** — single-user login protecting private pages.
- **Docker** — list of running containers (private).
- **Alerts** — Telegram notifications when the CPU runs hot or the machine has problems.

## Tech stack

FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL (psycopg 3) · pydantic-settings · managed with [uv](https://docs.astral.sh/uv/).

## Development

```bash
uv sync                          # install dependencies
uv run fastapi dev src/main.py   # run the dev server
uv run pytest                    # run tests
uv run ruff check .              # lint
```
