FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src

RUN useradd --system --no-create-home app
USER app

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

WORKDIR /app/src

# Exactly one worker is a HARD invariant (not just an optimization):
#  - the APScheduler lifespan runs the metrics sampler AND the reminder scan;
#    extra workers would duplicate samples and double-fire reminders.
#  - the reminder claim is a guarded UPDATE with no row lock (security M-2); it is
#    only correct under a single process. Never raise --workers above 1 without
#    adopting SELECT ... FOR UPDATE SKIP LOCKED in reminders/job.py first.
# Migrations run before the app serves (security M-4): DB must be healthy first
# (compose depends_on condition: service_healthy gates this).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers"]
