# chevlab-backend

Backend API for a homelab monitoring dashboard. Collects system metrics from the host machine, stores their history in PostgreSQL, and serves them to a web frontend.

## Features

- **System metrics:** CPU, memory, swap, disk, CPU temperature, and network rates, sampled every 5 seconds.
- **History:** stored samples can be queried by hour and are kept for 7 days.
- **Authentication:** single-user login with JWT bearer tokens.
- **Docker:** list of running containers, read through a restricted socket proxy.
- **Planner (calendar + to-do):** capture tasks in natural language, keep a calendar of dated events, and get lead-time reminders over Telegram and in-app.

Telegram alerts for system metrics are planned but not implemented yet; Telegram is currently used only by the planner's reminders.

## Tech stack

FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL (psycopg 3) · APScheduler · psutil · pydantic-settings · Ollama + dateparser (natural-language parsing) · managed with [uv](https://docs.astral.sh/uv/).

## API

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/healthz` | No | Liveness check |
| `GET` | `/api/readyz` | No | Readiness check, including the database |
| `GET` | `/api/info` | No | Host information |
| `GET` | `/api/latest` | No | Most recent metric sample |
| `GET` | `/api/history?hours=N` | No | Samples from the last `N` hours, oldest first. `N` is 1-168, default 1 |
| `POST` | `/api/login` | No | Exchange username and password for an access token, valid for 1 hour |
| `GET` | `/api/containers` | Yes | Running Docker containers |

### Planner (calendar + to-do)

Every planner route is private (send `Authorization: Bearer <token>`). All datetimes are UTC ISO-8601 on the wire; a request datetime must carry an offset or it is rejected with `422`.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/planner/todos` | Create a to-do. If `start_at` is given, it also creates a linked calendar event and schedules reminders |
| `POST` | `/api/planner/todos/parse` | Natural-language capture: extract title, date and reminder from free text, then create the to-do |
| `GET` | `/api/planner/todos?done=&date=&limit=&offset=` | List to-dos, filterable by completion and calendar day (day is interpreted in `PLANNER_RESOLVE_TZ`) |
| `GET` | `/api/planner/todos/{id}` | Fetch one to-do |
| `PATCH` | `/api/planner/todos/{id}` | Update a to-do; moving its date re-schedules reminders, clearing the date removes its event |
| `DELETE` | `/api/planner/todos/{id}` | Delete a to-do and its owned event and reminders |
| `GET` | `/api/planner/events?from=&to=` | Calendar events overlapping the `from`/`to` window (both required) |
| `GET` | `/api/planner/reminders?limit=` | In-app feed: reminders that have fired and are not yet acknowledged |
| `POST` | `/api/planner/reminders/{id}/ack` | Acknowledge (dismiss) a reminder so it stops appearing in the feed |

**Natural-language parsing.** `POST /todos/parse` sends the text to a small language model (Ollama, model `OLLAMA_MODEL`) that extracts only a title and rough date/reminder phrases; [`dateparser`](https://dateparser.readthedocs.io/) then resolves those phrases to concrete times in `PLANNER_RESOLVE_TZ` (Asia/Bangkok by default) and stores them as UTC. Thai and English are supported. The response lists any `assumptions` the system filled in (for example, a date with no time becomes an all-day task). The parser degrades gracefully: if a phrase is ambiguous the endpoint returns `422` with a candidate to confirm, and if the model is down, times out, or is not configured it returns `503` — in all of these cases nothing is persisted, and the rest of the API keeps working. A cloud fallback (Claude) is available by setting `CALENDAR_PARSER=claude`.

**Reminders.** A dated to-do gets a reminder with a default lead time of one day (`REMINDER_DEFAULT_LEAD`) unless the text or request says otherwise. A background job on the shared scheduler scans due reminders every `REMINDER_SCAN_INTERVAL_S` seconds and fans each one out to Telegram (if configured) and the in-app feed; firing is exactly-once and survives restarts, and reminders missed during downtime are caught up within `REMINDER_CATCHUP_WINDOW` (older ones expire). Fired reminders are pruned after `REMINDER_RETENTION_DAYS`; to-dos and events are kept indefinitely.

Send the token as `Authorization: Bearer <token>`. Timestamps are in UTC.

## Configuration

Copy `.env.example` to `.env` and fill in the values.

| Variable | Description |
|---|---|
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Database credentials |
| `POSTGRES_HOST`, `POSTGRES_PORT` | Database address. Compose overrides the host for the `api` service |
| `SECRET_KEY` | JWT signing key, at least 32 characters |
| `ADMIN_USERNAME` | Login username |
| `ADMIN_PASSWORD_HASH` | Argon2 hash of the login password |
| `TUNNEL_TOKEN` | Cloudflare Tunnel token, used only by the `cloudflared` service |

The planner adds its own variables (natural-language parser, reminders, and Telegram). Sensible defaults ship in `.env.example`, so you only need to change them to point at a different Ollama host/model, switch to the Claude fallback, or enable Telegram delivery (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). See `.env.example` for the full list with defaults and [`docs/design/calendar-todo/08-deployment.md`](docs/design/calendar-todo/08-deployment.md) for deployment detail.

Generate the secrets:

```bash
# SECRET_KEY
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"

# ADMIN_PASSWORD_HASH (prompts for the password)
uv run python -c "from getpass import getpass; from argon2 import PasswordHasher; print(PasswordHasher().hash(getpass()))"
```

Wrap `ADMIN_PASSWORD_HASH` in single quotes in `.env`. Without them, Compose treats each `$` as a variable and login fails. Quote any other value that contains `$` in the same way.

## Development

Requires Python 3.12+, uv, and Docker. The planner's natural-language parser also needs [Ollama](https://ollama.com/) running with the configured model pulled (`ollama pull qwen2.5:3b-instruct`); the rest of the API works without it, and that path returns `503` until Ollama is up. Alternatively set `CALENDAR_PARSER=claude` with an `ANTHROPIC_API_KEY` to use the cloud fallback instead.

```bash
uv sync                                # install dependencies
docker compose up -d db                # start PostgreSQL
cd src && uv run alembic upgrade head  # apply migrations (creates the planner tables)
cd ..
ollama serve                           # optional: enables POST /api/planner/todos/parse
uv run fastapi dev src/main.py         # run the dev server on http://localhost:8000
```

Interactive API docs are available at `/docs`.

```bash
uv run pytest                                              # run tests
uv run ruff check .                                        # lint
uv run ruff format .                                       # format
cd src && uv run alembic revision --autogenerate -m "msg"  # create a migration
```

Imports are rooted at `src/`, for example `from core.config import settings`. New models must be imported in `src/migrations/env.py` so that autogenerate can see them.

## Deployment

The backend runs on a Linux host with Docker Compose, and Cloudflare Tunnel makes it reachable from the internet without opening a port on the router. The frontend is deployed separately on Cloudflare and proxies `/api/*` to this backend.

```text
Browser → Frontend on Cloudflare → Cloudflare Tunnel → cloudflared → api (127.0.0.1:8000)
```

| Service | Purpose |
|---|---|
| `db` | PostgreSQL, with data in the `postgres_data` volume |
| `api` | FastAPI app. Applies migrations on startup and uses host networking to read the host's network interfaces and hostname |
| `socket-proxy` | Read-only access to the container list, on `127.0.0.1:2375` |
| `cloudflared` | Outbound connection to Cloudflare Tunnel |

### First deployment

1. In Cloudflare Zero Trust, go to **Networks → Tunnels**, create a tunnel, and copy its token.
2. Add a route from your hostname to the service `http://localhost:8000`. Never route to port `2375`, because that exposes the Docker API.
3. On the host, clone this repository and create `.env` as described in [Configuration](#configuration).
4. Start the stack:

   ```bash
   chmod 600 .env
   docker compose up -d --build
   ```

5. Set `BACKEND_URL=https://<your-hostname>` in the frontend's build environment.

### Verify

```bash
docker compose ps
curl http://127.0.0.1:8000/api/readyz
docker compose logs cloudflared | grep -i registered
curl https://<your-hostname>/api/readyz
```

Both `curl` commands should return `{"status":"ready"}`, and the logs should show `Registered tunnel connection`.

### Update

```bash
git pull
docker compose up -d --build
```

Migrations run automatically, and data in the volume is kept. Do not run `docker compose down -v` unless you intend to delete all stored metrics.

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Provided Tunnel token is not valid` | `TUNNEL_TOKEN` must contain only the token, starting with `eyJ` |
| Browser cannot create a secure connection | The Cloudflare certificate is not active. Check **SSL/TLS → Edge Certificates** |
| Cloudflare error 1033 | `cloudflared` is not connected |
| Cloudflare error 502 | The route does not point to `http://localhost:8000`, or `api` is down |
| Login returns 401 with the correct password | `ADMIN_PASSWORD_HASH` is not in single quotes |
| `/api/containers` returns 503 | `socket-proxy` is not running |

### Limitations

- Run only one `api` process. The scheduler runs inside the app, so extra workers would store duplicate samples.
- Disk usage is read from `/` inside the container, which reports the filesystem holding Docker's data. It may not match the host's root disk if Docker's data is on another disk.
- Network rates include the loopback interface, so they read higher than real network traffic.

### Security

The tunnel makes the API reachable from the internet. Before enabling it, be aware that:

- `/api/info`, `/api/latest`, `/api/history`, and `/docs` do not require login.
- `/api/login` has no rate limit, so use a long password and add a Cloudflare rate limiting rule.
- `db` publishes PostgreSQL on all interfaces. If nothing else needs it, bind it to `127.0.0.1` in `docker-compose.yml`.
