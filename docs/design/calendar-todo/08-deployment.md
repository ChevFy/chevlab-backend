# Calendar + To-Do — Deployment & Ops (Stage 08)

- **Feature slug:** `calendar-todo`
- **Stage:** 08 — DevOps & Deployment
- **Date:** 2026-11-15
- **Target host:** Lenovo ThinkCentre M710q — 8 GB RAM, no GPU, CPU-only.
- **Security gate:** Stage 07 is **CLEARED**. H-1 (Telegram token in logs) and
  M-1 (reminder marked `sent` without delivery) are fixed; 114 tests pass.
  Residual notes M-2/M-3/M-4 are carried into this runbook (see Ops notes).

This feature adds, on top of the existing system-monitoring deployment:
a local **Ollama SLM** (`qwen2.5:3b-instruct`) for `/todos/parse`, `dateparser`,
`httpx` (promoted to main deps), two new APScheduler jobs (reminder scan +
daily prune), and a set of new env vars. **It does not change the existing
db / socket-proxy / api / cloudflared services' behavior.**

---

## 1. Infra changes made in this stage

| File | Change |
|------|--------|
| `docker-compose.yml` | Added `ollama` service (`ollama/ollama:latest`), bound to `127.0.0.1:11434`, `mem_limit: 4g`, `OLLAMA_KEEP_ALIVE` / `OLLAMA_NUM_PARALLEL=1` / `OLLAMA_MAX_LOADED_MODELS=1`, healthcheck, `ollama_data` volume. `api.depends_on` now also waits on `ollama` (`service_started`, non-blocking). |
| `Dockerfile` | `CMD` now passes `--workers 1` explicitly and documents the single-worker **hard invariant** (M-2) and the migrate-before-serve ordering (M-4). No build-layer change. |
| `.github/workflows/ci.yml` | **New.** Lint (`ruff check`) + format check + `pytest` on SQLite, then a build-only Docker smoke step. |
| `.env.example` / `src/core/config.py` | Already complete from stage 05 — verified in sync (see §4). No edits needed. |

### Why Ollama as a container (not host-installed)

Reproducible, versioned, memory-capped, and started/stopped with the rest of the
stack. The `api` runs with `network_mode: host`, so it reaches the container-
published Ollama at `localhost:11434` — exactly the `OLLAMA_HOST` default. No
code change was needed to wire it. Host-install is a valid alternative (set
`OLLAMA_HOST` to the host service) but loses the compose lifecycle and the
`mem_limit` guard.

### Memory budget (8 GB box)

| Process | Approx RAM |
|---|---|
| Postgres 17 | ~0.3–0.5 GB |
| api (FastAPI + sampler + scheduler) | ~0.3 GB |
| Ollama + `qwen2.5:3b-instruct` resident | ~2.3–3 GB (capped at `mem_limit: 4g`) |
| socket-proxy / cloudflared | negligible |
| OS + Docker | ~1 GB |

`OLLAMA_KEEP_ALIVE=5m` unloads the model when the operator is not actively
parsing, returning ~2.3 GB to the box most of the time. `OLLAMA_NUM_PARALLEL=1`
+ `OLLAMA_MAX_LOADED_MODELS=1` serialize model work so a burst of parses cannot
load multiple copies and OOM the box (partial mitigation for M-3 at the Ollama
layer; see Ops §7).

---

## 2. Migrations on deploy (security M-4 — REQUIRED)

The planner migration `a1b2c3d4e5f6_add_planner_calendar_todo_tables` was
hand-authored (no Postgres in the stage-05 sandbox) and has **never run on a real
database**. It MUST be applied before the app serves, and its schema confirmed
against the models.

The `api` container runs it automatically on start:

```
alembic upgrade head && exec uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers
```

Ordering is guaranteed by compose: `api.depends_on.db.condition: service_healthy`
→ Postgres is accepting connections before `alembic upgrade head` runs → app
serves. **Do not** scale `api` to multiple replicas (the migrate step would race;
also violates M-2 below).

**One-time verification on first deploy (M-4 gate):**

```bash
# after the stack is up, from the repo on the host:
cd src && uv run alembic upgrade head          # should report "already at head"
cd src && uv run alembic revision --autogenerate -m "verify" --sql | head
#   ^ should produce an EMPTY diff; if it emits DDL, the migration drifts from models — STOP.
# smoke-test DB-level cascade:
#   create a todo with a reminder, DELETE /api/planner/todos/{id}, confirm its
#   reminders + reminder_deliveries rows are gone (ON DELETE CASCADE works).
```

---

## 3. Single-worker constraint (security M-2 — HARD INVARIANT)

The reminder scheduler runs **inside the app's FastAPI lifespan** on a shared
`AsyncIOScheduler` (`src/modules/scheduler/scheduler.py`). The reminder claim is
a guarded `UPDATE ... WHERE status='pending'` with a `rowcount==1` check and **no
row lock**. This is correct **only in a single process / single event loop**.

Therefore:

- The container `CMD` pins `uvicorn --workers 1`. **Never raise this.**
- **Never** run more than one `api` replica.
- Extra workers/replicas would: (a) duplicate the 5s metrics sampler, (b)
  double-fire reminders, (c) expose the claim race in M-2.
- If horizontal scaling is ever required, first switch the claim to
  `SELECT ... FOR UPDATE SKIP LOCKED` in `reminders/job.py` **and** move the
  scheduler out of per-worker lifespan into a single dedicated process.

---

## 4. Environment variables

`.env.example` is authoritative and in sync with `src/core/config.py` (verified).
Copy it to `.env` and fill real values. New/relevant vars for this feature:

### Required (app will not boot without these — existing)
| Var | Purpose |
|---|---|
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | DB connection. |
| `POSTGRES_HOST` | Set to `127.0.0.1` for the host-networked api (compose overrides it). |
| `POSTGRES_PORT` | Default `5432`. |
| `SECRET_KEY` | JWT signing; `secrets.token_urlsafe(48)`. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` | Single-user login (argon2 hash). |
| `TUNNEL_TOKEN` | Cloudflare tunnel. |

### Planner — NL parser (new)
| Var | Default | Req? | Notes |
|---|---|---|---|
| `CALENDAR_PARSER` | `ollama` | required | `ollama` (local, default) or `claude` (cloud). |
| `OLLAMA_HOST` | `http://localhost:11434` | required when parser=ollama | Matches the compose-published Ollama. |
| `OLLAMA_MODEL` | `qwen2.5:3b-instruct` | required when parser=ollama | Must be pulled (see §5). |
| `OLLAMA_KEEP_ALIVE` | `5m` | optional | Short → model unloads when idle (frees ~2.3 GB). Also read by the ollama container. |
| `PARSE_TIMEOUT_S` | `15` | optional | Per-parse hard timeout → 503 on exceed. |
| `PARSE_MAX_INPUT_CHARS` | `500` | optional | Note I-1: effective cap is the 500-char pydantic schema; this knob is informational. |
| `ANTHROPIC_API_KEY` | _(unset)_ | **optional** | Only used when `CALENDAR_PARSER=claude`. Keep commented. |
| `CLAUDE_MODEL` | `claude-haiku-4-5` | optional | Cloud fallback model. |
| `PLANNER_RESOLVE_TZ` | `Asia/Bangkok` | required | Timezone input is resolved in before storing UTC. |

### Planner — reminders (new)
| Var | Default | Req? | Notes |
|---|---|---|---|
| `REMINDER_DEFAULT_LEAD` | `P1D` | optional | ISO-8601 duration applied when none stated. |
| `REMINDER_SCAN_INTERVAL_S` | `30` | optional | Scan cadence. |
| `REMINDER_CATCHUP_WINDOW` | `PT6H` | optional | Deliver a missed reminder only if less late than this. |
| `REMINDER_CLAIM_GRACE` | `PT5M` | optional | Stale `sending` re-claim window after a crash. |
| `REMINDER_RETENTION_DAYS` | `30` | optional | Daily prune of fired reminders. |

### Telegram (notify module — OPTIONAL, graceful degrade)
| Var | Default | Req? | Notes |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | _(unset)_ | **optional** | If unset, the Telegram channel is skipped; in-app reminders still fire. Keep commented. |
| `TELEGRAM_CHAT_ID` | _(unset)_ | **optional** | Pair with the token. |

**Graceful degradation:** `ANTHROPIC_API_KEY` and `TELEGRAM_*` are optional. With
them unset the app runs fully; parse uses Ollama, reminders deliver in-app only.
If **Ollama** is down, the app still boots and only `/todos/parse` returns 503.

---

## 5. Deploy runbook (ordered)

Run on the ThinkCentre host, from the repo root, with a filled `.env`.

```bash
# 1. Pull / sync code.
git pull

# 2. Validate compose renders (catches typos before touching running services).
docker compose config >/dev/null

# 3. Bring up DB + Ollama + socket-proxy first (DB must be healthy for migrate).
docker compose up -d db ollama socket-proxy

# 4. Pull the SLM model INTO the running ollama container (one-time; persisted in
#    the ollama_data volume thereafter). ~2.3 GB download.
docker compose exec ollama ollama pull qwen2.5:3b-instruct
docker compose exec ollama ollama list          # confirm the model is present

# 5. Build + start the api. Its entrypoint runs `alembic upgrade head` (M-4)
#    before uvicorn serves. db is already healthy, so migration ordering holds.
docker compose up -d --build api

# 6. Start the tunnel.
docker compose up -d cloudflared

# 7. Verify.
docker compose ps                                # all healthy
curl -s http://127.0.0.1:8000/api/readyz         # {"status":"ready"}
#    (optional) log in and POST /api/planner/todos/parse with a sample line.

# 8. First-deploy-only: run the M-4 migration verification + cascade smoke test (§2).
```

Steps 3→5 enforce the required ordering: **DB up/healthy → migrate (inside api
start) → app serves.** Ollama is up before the api so the parse path is usable
immediately; if you skip the model pull, `/todos/parse` returns 503 until it is
pulled (app stays healthy).

---

## 6. Rollback

The feature is **additive**: new tables (`todos`, `events`, `reminders`,
`reminder_deliveries`) and new routes under `/api/planner`. Existing
system-monitoring data and schema are untouched.

**Rollback options (least to most destructive):**

1. **App-only rollback (recommended).** Redeploy the previous image tag / commit:
   ```bash
   git checkout <previous-good-commit>
   docker compose up -d --build api
   ```
   The planner tables remain (harmless, unreferenced by old code). The new
   scheduler jobs simply stop registering. Fastest, no data loss.

2. **Schema rollback (only if the migration itself is bad).**
   ```bash
   cd src && uv run alembic downgrade -1   # drops the 4 planner tables
   ```
   Destroys all todos/events/reminders. The migration drops in reverse FK order
   (`reminder_deliveries → reminders → todos → events`). Do this only if the
   migration DDL is defective; otherwise prefer option 1.

3. **Disable Ollama without rollback.** If the SLM is misbehaving / starving the
   box, `docker compose stop ollama`. The app stays up; `/todos/parse` returns
   503 until Ollama is back. (Or set `CALENDAR_PARSER=claude` + an
   `ANTHROPIC_API_KEY` to move parsing to the cloud.)

Rollback does **not** require touching the db/socket-proxy/cloudflared services.

---

## 7. Ops notes & residual risks

- **M-3 (parse concurrency cap) — partially mitigated, follow-up noted.** There is
  a bounded per-parse timeout (`PARSE_TIMEOUT_S`) and the app is single-operator,
  single-worker. At the Ollama layer, `OLLAMA_NUM_PARALLEL=1` /
  `OLLAMA_MAX_LOADED_MODELS=1` + `mem_limit: 4g` prevent runaway model loads.
  There is still **no application-level in-flight cap** on `/todos/parse`; a
  looping client can queue parses inside Ollama. Recommended fast-follow: an
  `asyncio.Semaphore` (1–2) around the parser call that fails fast with 503.
  Operationally: watch for memory pressure; the `mem_limit` is the hard backstop.
- **Log hygiene (H-1 fixed).** The Telegram token no longer reaches logs. Keep it
  that way — do not log raw httpx exceptions for the Telegram path. Cloudflare-
  tunnel exposure means logs may be retained/shipped.
- **Healthchecks.** `db` → `pg_isready`; `api` → `/api/readyz`; `ollama` →
  `ollama list`. `docker compose ps` should show all healthy post-deploy.
- **Restart policy.** `restart: unless-stopped` on ollama/socket-proxy/api/
  cloudflared; db relies on healthcheck ordering. After a host reboot the stack
  self-recovers; the reminder scan catches up missed reminders within
  `REMINDER_CATCHUP_WINDOW` (durable in Postgres, not APScheduler state).
- **DB growth.** Metrics sampler (5s) + reminder rows; planner reminders are
  pruned daily (`REMINDER_RETENTION_DAYS`), todos retained intentionally. The
  `(status, fire_at)` index keeps the scan cheap as history grows.

---

## 8. CI pipeline

New `.github/workflows/ci.yml`, on push to `main`/`develop` and all PRs:

1. `ruff check .` — lint.
2. `ruff format --check .` — format gate.
3. `pytest` — full suite on in-memory SQLite (aiosqlite); **no Postgres, Ollama,
   or network required**, so CI needs no services.
4. Build-only Docker image smoke (`docker build`) — keeps the Dockerfile
   buildable. Not pushed; deploy is a manual `docker compose up -d --build` on the
   box.

**Known pre-existing failures CI will surface (NOT introduced by this feature —
for stage 09 to clear):**
- `src/modules/health/router.py:19` — a pre-existing `BLE001` lint error
  (documented since stage 05).
- Several `.md` docs and a few `tests/` files are not `ruff format`-clean (8–9
  files). These predate stage 08 and were not reformatted here to avoid churning
  other stages' artifacts. Stage 09 should run `uv run ruff format .` and resolve
  the `BLE001` so CI goes green on the first run.

Locally verified this stage: `docker compose config` (exit 0, ollama service
renders), `uv run pytest` → **114 passed**. Docker image build was **not** run
locally (no Docker daemon in the sandbox); the `CMD`-only change does not affect
build layers.

---

## Handoff — to Code Review & Documentation Agent (stage 09)

**Infra changes made:**
- `docker-compose.yml`: new `ollama` service (CPU-only, `127.0.0.1:11434`,
  `mem_limit: 4g`, keep-alive/parallel/max-models tuned, healthcheck,
  `ollama_data` volume); `api.depends_on` waits on ollama (non-blocking).
- `Dockerfile`: `CMD` pins `--workers 1` + documents the single-worker invariant
  (M-2) and migrate-before-serve (M-4). No build-layer change.
- `.github/workflows/ci.yml`: new lint + format + pytest + image-build pipeline.
- `.env.example` / `config.py`: verified complete and in sync (no edits).

**New env vars (feature):** `CALENDAR_PARSER`, `OLLAMA_HOST`, `OLLAMA_MODEL`,
`OLLAMA_KEEP_ALIVE`, `PARSE_TIMEOUT_S`, `PARSE_MAX_INPUT_CHARS`,
`ANTHROPIC_API_KEY`(opt), `CLAUDE_MODEL`, `PLANNER_RESOLVE_TZ`,
`REMINDER_DEFAULT_LEAD`, `REMINDER_SCAN_INTERVAL_S`, `REMINDER_CATCHUP_WINDOW`,
`REMINDER_CLAIM_GRACE`, `REMINDER_RETENTION_DAYS`, `TELEGRAM_BOT_TOKEN`(opt),
`TELEGRAM_CHAT_ID`(opt). Telegram + Anthropic are optional (graceful degrade).

**Deploy/rollback:** ordered runbook in §5 (DB+Ollama up → pull model → migrate-
on-api-start → tunnel → verify → M-4 gate). Rollback in §6 (prefer app-only
redeploy; schema `alembic downgrade -1` only if the migration is defective).

**Stage 09 should verify:**
1. **Make CI green:** run `uv run ruff format .` and fix the pre-existing
   `health/router.py` `BLE001` — these pre-date stage 08 and block the new CI.
2. The M-4 deploy gate (migration applied + empty autogenerate diff + cascade
   smoke) is executed on the real Postgres at go-live and recorded.
3. Final docs (README / deploy section) reference this runbook and the
   single-worker invariant.
4. Confirm the M-3 follow-up (parse concurrency semaphore) is tracked as known
   future work.

**State: ready for stage 09.**
