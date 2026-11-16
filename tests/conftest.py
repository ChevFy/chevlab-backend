"""Shared pytest fixtures for the planner (calendar-todo) test suite.

Environment note (stage 06): there is NO Postgres/Docker in this sandbox, so the
whole suite runs against an in-memory async SQLite DB (aiosqlite). Tables are
created from the SQLAlchemy metadata (``Base.metadata.create_all``) rather than
Alembic. Three SQLite-vs-Postgres gaps are bridged on the test engine's dialect,
test-side only, without touching production ``src/`` code:

1. ``BigInteger`` primary keys do not autoincrement in SQLite. A dialect-level
   ``@compiles`` hook emits ``INTEGER`` for SQLite so ``INTEGER PRIMARY KEY``
   autoincrements, matching Postgres ``BIGSERIAL`` behaviour.
2. SQLite drops the tzinfo on ``DateTime(timezone=True)`` columns, returning
   naive datetimes (for both ORM objects AND bare-column selects). A colspec
   override makes every datetime result come back UTC-aware — matching Postgres
   ``timestamptz`` — so the firing job and the reminder-feed ``is_late`` math
   (which mix an aware ``now`` with DB values) behave exactly as in production.
3. ``INTERVAL`` has no SQLite equivalent; SQLAlchemy's default emulation reuses
   the DateTime epoch machinery, which the tz-aware override breaks. A simple
   seconds-as-float ``TypeDecorator`` is substituted for ``Interval`` on SQLite,
   round-tripping ``timedelta`` cleanly.

The module-level ``SessionLocal`` used throughout ``service.py`` / ``job.py`` is
monkeypatched to the test sessionmaker (the code reads it via ``from
core.database import SessionLocal`` into each module, so each reference is
patched).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import BigInteger, DateTime, Interval, types
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# --- SQLite dialect bridges (test-only) -----------------------------------


@compiles(BigInteger, "sqlite")
def _bigint_as_integer_sqlite(element, compiler, **kw):
    # INTEGER PRIMARY KEY autoincrements in SQLite; BIGINT does not.
    return "INTEGER"


class _TzAwareDateTime(SQLITE_DATETIME):
    """Return every datetime column UTC-aware, like Postgres ``timestamptz``."""

    def result_processor(self, dialect, coltype):
        inner = super().result_processor(dialect, coltype)

        def process(value):
            dt = inner(value) if inner is not None else value
            if dt is not None and dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt

        return process


class _SecondsInterval(types.TypeDecorator):
    """Store ``Interval`` as seconds-float on SQLite (native epoch math off)."""

    impl = types.Float
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return value.total_seconds() if value is not None else None

    def process_result_value(self, value, dialect):
        return timedelta(seconds=value) if value is not None else None


def _apply_sqlite_colspecs(engine) -> None:
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {
        **dialect.colspecs,
        DateTime: _TzAwareDateTime,
        Interval: _SecondsInterval,
    }


# --- DB engine / session wiring -------------------------------------------


@pytest_asyncio.fixture
async def session_local(monkeypatch):
    """A fresh in-memory SQLite DB per test, with ``SessionLocal`` patched in.

    Yields the test ``async_sessionmaker`` so service-level unit tests can open
    their own sessions identically to production code.
    """
    from core.models import Base

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _apply_sqlite_colspecs(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    TestSession = async_sessionmaker(engine, expire_on_commit=False)

    # Patch every module that bound ``SessionLocal`` at import time.
    from core import database
    from modules.planner import service
    from modules.planner.reminders import job

    monkeypatch.setattr(database, "SessionLocal", TestSession)
    monkeypatch.setattr(service, "SessionLocal", TestSession)
    monkeypatch.setattr(job, "SessionLocal", TestSession)

    yield TestSession

    await engine.dispose()


# --- Auth / app fixtures ---------------------------------------------------


@pytest.fixture
def auth_user():
    return "test-operator"


@pytest.fixture
def app(session_local, auth_user):
    """The real FastAPI app with ``get_current_user`` overridden (authenticated)."""
    from main import app as fastapi_app
    from modules.auth.jwt import get_current_user

    fastapi_app.dependency_overrides[get_current_user] = lambda: auth_user
    yield fastapi_app
    fastapi_app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def client(app):
    """Authenticated TestClient (lifespan disabled; scheduler not started)."""
    from fastapi.testclient import TestClient

    # Do NOT enter lifespan: it would start the real APScheduler + hit Postgres.
    return TestClient(app)


@pytest.fixture
def unauth_client(session_local):
    """A client with NO auth override — exercises the 401/403 gate."""
    from fastapi.testclient import TestClient

    from main import app as fastapi_app

    return TestClient(fastapi_app)


# --- helpers ---------------------------------------------------------------


def utcnow() -> datetime:
    return datetime.now(UTC)
