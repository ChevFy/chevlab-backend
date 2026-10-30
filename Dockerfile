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

# One process only: the scheduler runs in the app lifespan, extra workers would duplicate samples
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 127.0.0.1 --port 8000 --proxy-headers"]
