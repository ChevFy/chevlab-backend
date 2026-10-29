# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Homelab system-monitoring backend (FastAPI). Collects metrics from the host machine and serves them to a web frontend. Planned features:
- **Live metrics** — CPU, RAM, DISK, temperature, uptime (`psutil`); frontend auto-refreshes every 2-3s.
- **History graphs** — 1hr / 24hr windows; a background job samples metrics on an interval and writes to the DB.
- **Online/offline** — status + offline page, derived from heartbeat / last-sample freshness.
- **Auth** — single-user login gating private pages (JWT or session).
- **Docker** — list of running containers (private, via `docker` SDK).
- **Alerts** — Telegram notification when CPU runs hot or the machine has problems.

## Status

Early scaffold. Only `src/main.py` (a stub `main()` printing a greeting) exists as real code. `README.md`, `.env.example`, and `tests/` are empty. The `src/core/` and `src/modules/{auth,health,notify}/` directories are placeholders holding only `.gitkeep`. Most structure below is the intended layout, not yet implemented — verify files exist before relying on them.

## Tooling

Python 3.12+, managed with `uv` (see `uv.lock`, `.python-version`). FastAPI backend stack: SQLAlchemy 2 + Alembic (migrations) + psycopg 3 (Postgres driver) + pydantic-settings (config).

## Commands

```bash
uv sync                          # install deps (incl. dev group)
uv run fastapi dev src/main.py   # run FastAPI dev server (from repo root)
cd src && uv run python main.py  # alt: run via uvicorn entrypoint
uv run ruff check .              # lint
uv run ruff format .             # format
uv run pytest                    # run tests
uv run pytest path/to/test.py::test_name   # run a single test
cd src && uv run alembic upgrade head      # apply DB migrations (alembic.ini lives in src/)
cd src && uv run alembic revision --autogenerate -m "msg"   # create a migration
```

Imports are rooted at `src/` (`from core...`, `from modules...`), not `src.` — `src/` is not a package. Run Python entrypoints with `src/` as cwd or on `sys.path`. New models must be imported in `src/migrations/env.py` for autogenerate to see them.

`httpx` is a dev dep — use FastAPI's `TestClient` / httpx for endpoint tests.

## Intended architecture

Module-per-feature layout under `src/`:
- `src/core/` — shared infra (settings via pydantic-settings, DB session/engine, base SQLAlchemy models, common deps).
- `src/modules/<feature>/` — self-contained feature slices. Each slice owns its router, schemas, models, and service logic; `src/main.py` composes the FastAPI app by mounting each module's router. Feature → module:
  - `health` — live metrics + history endpoints + online/offline status.
  - `auth` — single-user login, provides the dependency that gates private endpoints.
  - `notify` — Telegram alerting.
  - `docker` (to add) — running-container listing.

Cross-cutting pieces:
- **Background sampler** — a long-running task (FastAPI lifespan / `asyncio` loop) reads metrics every ~2-3s, persists a time-series row, and runs the alert threshold check (with debounce so one hot spike ≠ repeated Telegram messages). This feeds both the history graphs and the online/offline heartbeat.
- **DB growth** — a 2-3s sample rate is ~1200 rows/hr/metric. Plan retention: keep raw samples ~24h, roll up older data hourly, or the table grows unbounded.
- **Private endpoints** (history, docker list) sit behind the `auth` dependency.

Config comes from env (`.env`, template in `.env.example`) loaded through pydantic-settings — do not read `os.environ` directly.

## Dependencies to add

`pyproject.toml` currently lacks several the feature set needs: `psutil` (metrics), `docker` (container list), password hashing (`pwdlib[bcrypt]` or `passlib`+`bcrypt`) and `pyjwt` (auth). Telegram alerts can use `httpx` (currently dev-only — move to main deps).
