# Calendar + To-Do — Security & Reliability Review (Stage 07)

- **Feature slug:** `calendar-todo`
- **Stage:** 07 — Security & Reliability Audit (REVIEW ONLY — no production code changed)
- **Date:** 2026-11-15
- **Auditor:** Security & Reliability Agent
- **Inputs:** `01`–`06` design docs (approved), implemented `src/modules/planner/`,
  `src/modules/notify/`, `src/modules/scheduler/scheduler.py`, `src/core/config.py`,
  `src/main.py`, migration `a1b2c3d4e5f6_add_planner_calendar_todo_tables.py`.
- **Scope note:** This is a read-only audit. Findings below are recommendations;
  no production code was modified.

---

## TL;DR

**No Critical issues. One High (secret leak into logs) must be fixed before
ship.** The feature is well-architected: all 9 endpoints are auth-gated at the
router level, the exactly-once reminder gate is correct, inputs are bounded,
secrets come from pydantic-settings, `.env` is gitignored and untracked, and all
DB access is ORM/parameterized (no raw SQL). The dominant risk is a **Telegram
bot-token leak into application logs** on any Telegram HTTP error, which directly
contradicts the stated NFR-21 ("secrets never logged").

**Verdict: BLOCKED on one High finding (H-1). Fix H-1, then cleared for stage 08.**
H-1 is a one-line change; the Mediums are hardening and can ship as fast-follows
if the operator accepts the documented risk.

---

## Severity summary

| ID | Severity | Area | Title |
|----|----------|------|-------|
| H-1 | **High** | Secrets | Telegram bot token leaked to logs on HTTP-status error |
| M-1 | Medium | Reliability | Reminder marked `sent` even when every channel failed (false "exactly-once success") |
| M-2 | Medium | Reliability | No DB row lock on claim — `SELECT`-then-`UPDATE` relies on per-row commit; correct today, fragile if a second worker is ever added |
| M-3 | Medium | Resource/DoS | `/todos/parse` has no per-user rate limit / in-flight cap; a hanging Ollama call can pin a model-load slot and starve the 8GB box |
| M-4 | Medium | Data integrity | Live-DB verification of migration + `ON DELETE CASCADE` + CHECK constraints never run (sandbox had no Postgres) |
| L-1 | Low | Error handling | `PATCH` maps any service `ValueError` to 422, risking a misleading 422 for an unrelated internal `ValueError` |
| L-2 | Low | Reliability | `_dispatch_one` reads `Todo.title` after claim without a null/absent guard beyond `or "task"`; a deleted todo mid-flight yields a degraded message (acceptable) |
| L-3 | Low | Input validation | `source_text`/`title` from the SLM are persisted unbounded-by-title-length only; no explicit sanitization of model-echoed content (XSS is a frontend concern, noted) |
| L-4 | Low | Resource | `claude_parser` builds a fresh `AsyncClient` per call; fine at single-user scale, noted |
| I-1 | Info | Config | `PARSE_MAX_INPUT_CHARS` setting is not actually enforced in code — the 500-char cap is the pydantic schema only |
| I-2 | Info | Correctness | `Channel` Protocol signature in `channels.py`/`delivery.py` drifts from concrete impls (no `session` param); cosmetic, works at runtime |
| I-3 | Info | Reliability | Retention prune never deletes `failed` reminders — intended (keep for diagnostics), confirm it is intended |

---

## Detailed findings

### H-1 — Telegram bot token leaked into logs on HTTP-status error  (High)

**Location:** `src/modules/notify/service.py:35,39-43`

```python
url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
...
resp.raise_for_status()
...
except httpx.HTTPError as exc:
    logger.warning("telegram send failed: %s", exc)   # <-- leaks the URL incl. token
```

**Impact.** When Telegram returns any non-2xx (invalid token → 401, chat not
found → 400, rate limit → 429, Telegram 5xx), `resp.raise_for_status()` raises
`httpx.HTTPStatusError`, whose `str()` embeds the **full request URL** — which
contains `TELEGRAM_BOT_TOKEN` in the path. Line 43 logs `%s` of that exception, so
the bot token lands in plaintext application logs. Confirmed empirically:

```
Client error '401 Unauthorized' for url
'https://api.telegram.org/bot123456:ABC-SECRET-TOKEN/sendMessage'
```

A leaked bot token lets an attacker send/read messages as the bot. This directly
contradicts the module docstring ("Secrets (bot token) are never logged", NFR-21)
and the design's secret-handling requirement. The box is Cloudflare-tunnel exposed
and logs may be shipped/retained, widening the blast radius. The most common
trigger — a misconfigured or rotated token (401) — is exactly the case that fires
this log line.

**Fix (recommended).** Never interpolate the token into a value that can be
stringified into logs. Either:
- Log a redacted message that never includes `exc` verbatim for status errors,
  e.g. `logger.warning("telegram send failed: HTTP %s", getattr(exc, "response", None) and exc.response.status_code)`; or
- Catch `httpx.HTTPStatusError` separately and log only `exc.response.status_code`;
  for transport errors log `type(exc).__name__` only; or
- Pass the token via header/separate arg patterns and scrub the URL, and add a
  logging filter that redacts `bot\d+:[\w-]+` from all records as defence-in-depth.

**Note:** The parser impls (`ollama_parser.py`, `claude_parser.py`) also log
`%s` of httpx exceptions, but their URLs (`OLLAMA_HOST`, the fixed Anthropic
endpoint) carry **no secret in the URL** — the Anthropic key is a header, which
httpx does **not** put in the exception string. So those log lines are safe;
only the Telegram path embeds the secret in the URL. **H-1 is isolated to
`notify/service.py`.**

---

### M-1 — Reminder marked `sent` even when every channel failed  (Medium)

**Location:** `src/modules/planner/reminders/job.py:81-89`

```python
for channel in CHANNELS:
    try:
        await channel.deliver(session, reminder, text=text)
    except Exception:
        logger.exception("channel %s raised during delivery", channel.name)

reminder.status = "sent"  # unconditional
reminder.sent_at = datetime.now(UTC)
```

**Impact.** The reminder transitions to `sent` regardless of per-channel outcome.
In practice the in-app channel never does network I/O and almost always succeeds,
so the operator still sees the reminder in the feed — this is the intended
graceful-degradation story (Telegram down ≠ lost reminder). **However**, if the
in-app `deliver` itself raises (e.g. a transient DB error on the delivery
sub-row), the exception is swallowed and the reminder is still marked `sent` with
**no delivery row written**. It then never re-fires (status gate) and never
surfaces in the feed — a silently lost reminder. This is the exact failure class
that BUG-2 (stage 06) already demonstrated once; the structural pattern
("catch-all + unconditional `sent`") still allows a recurrence from a different
cause.

**Fix (recommended).** Make the final status reflect channel results: mark `sent`
only if at least one channel succeeded (collect `ChannelResult.ok`), else mark
`failed` so the next scan (or an alert) can observe it. At minimum, do not swallow
an in-app-channel failure — the reliable fallback must be verified before
declaring success. Keep per-channel independence (Telegram failing alone should
not block). This also gives `reminder.status == "failed"` real meaning (it is
currently never set by the job).

---

### M-2 — Claim is `SELECT`-then-guarded-`UPDATE`, no row lock  (Medium)

**Location:** `src/modules/planner/reminders/job.py:37-69`

**Impact.** The scan does `_select_due` (plain `SELECT`) then, per row, a guarded
`UPDATE ... WHERE status='pending' ... ` and checks `rowcount == 1`. The guarded
UPDATE **is** the correctness gate and is atomic at the row level, so even if two
ticks select the same row only one UPDATE wins — this is correct for the current
**single-process, single-event-loop** deployment (A-5), and stage 06 verified it
under cooperative `asyncio.gather`. The residual risk is purely forward-looking:
if a second `api` worker/replica is ever added, two connections could each select
the row, both attempt the UPDATE, and one gets `rowcount==0` and skips — still
correct — **but** the window between `_select_due` and `_claim` is not covered by
`SELECT ... FOR UPDATE SKIP LOCKED`, so there is unnecessary contention and the
"stale `sending` re-claim" path could, under multi-worker clock skew, be claimed
twice within the grace. Not a bug today; a latent scaling hazard.

**Fix (recommended, low urgency).** Document the single-worker assumption as a
hard deployment invariant (it already is, A-5). If multi-worker is ever on the
table, switch `_select_due` to `.with_for_update(skip_locked=True)` and claim in
the same transaction, collapsing select+claim into one atomic step. No change
needed for v1 if single-worker is enforced at deploy.

---

### M-3 — `/todos/parse` has no in-flight cap / rate limit on an 8GB box  (Medium)

**Location:** `src/modules/planner/router.py:63-66`, `parser/ollama_parser.py`,
`src/core/config.py` (no concurrency cap setting)

**Impact.** Every authenticated `/todos/parse` call triggers one Ollama model
call. The model is ~2.3 GB and shares an 8GB box with Postgres, Docker, and the
5s metrics sampler. There is a bounded per-call timeout (`PARSE_TIMEOUT_S=15`,
good) but **no limit on concurrent in-flight parses**. A burst of parse requests
(or a single client looping) forces repeated model load/unload thrashing
(`keep_alive=5m` keeps it resident, so concurrent calls queue inside Ollama) and
can drive the box into memory pressure / OOM, degrading the metrics sampler and
DB. The attack surface is limited to the single authenticated operator (not
public), so this is a self-DoS / robustness issue, not an external DoS — hence
Medium, not High.

**Fix (recommended).** Add a small `asyncio.Semaphore` (e.g. 1–2 concurrent
parses, settings-driven) around the parser call so parses serialize and excess
requests fail fast with 503 rather than piling model loads. This also protects the
sampler's event-loop budget (NFR-19). Cheap, high value on this hardware.

---

### M-4 — Migration + DB-level cascade/CHECK never verified on live Postgres  (Medium)

**Location:** `src/migrations/versions/a1b2c3d4e5f6_*.py`, `models.py`,
stage-06 report "Coverage notes/gaps".

**Impact.** Tests ran on in-memory SQLite via `Base.metadata.create_all`, **not**
Alembic, and SQLite does not enforce FKs by default. So the hand-authored
migration DDL, the DB-level `ON DELETE CASCADE` (`reminders.todo_id`,
`reminder_deliveries.reminder_id`), the `ON DELETE SET NULL` (`todos.event_id`),
and all CHECK constraints (`status`, `lead_source`, `channel`) are **unverified
against a real database**. If the migration drifts from the models, or a cascade
is wrong, delete operations could orphan rows or FK-fail in production even though
the suite is green. Service-level cascade (delete todo → delete owned event, clear
pending reminders) is tested at the ORM layer, but the DB-FK safety net is not.

**Fix (required at deploy, stage 08).** Before go-live: `cd src && uv run alembic
upgrade head` against the real Postgres, then diff the resulting schema against the
models (e.g. `alembic revision --autogenerate` should produce an empty diff).
Smoke-test one `DELETE /todos/{id}` with reminders+deliveries and confirm cascade.
This is an environment gap carried from stage 06, not a code defect — but it is a
reliability precondition for ship.

---

### L-1 — `PATCH` coerces any service `ValueError` to 422  (Low)

**Location:** `src/modules/planner/router.py:141-144`, `service.py:299`

The handler maps `except ValueError` → 422. Today the only service-raised
`ValueError` is the intended "reminders require a dated todo". But a future
`ValueError` from deeper code (bug, bad data) would be masked as a client 422
instead of surfacing as a 500, hiding real faults. Low impact (no leak, no data
loss). **Fix:** raise a dedicated typed exception from the service for the
validation case and catch that specifically; let unexpected `ValueError` bubble to
the generic 500 handler.

### L-2 — Deleted-todo-mid-flight yields a degraded reminder message  (Low)

**Location:** `job.py:75`, `build_message` in `delivery.py`

`_dispatch_one` fetches `Todo.title` after the claim; if the todo was deleted
between select and dispatch, `title` is `None` → falls back to `"task"`. The
reminder's FK `ON DELETE CASCADE` means a deleted todo also deletes its reminders,
so this is a narrow race and the fallback is safe. Accepted risk; noted for
completeness.

### L-3 — SLM-echoed `title`/`source_text` persisted without content sanitization  (Low)

**Location:** `service.py:495-500`, `ollama_parser.py:93`

`title` (max 255 via the model column) and `source_text` (raw input, ≤500 chars by
the parse schema) are stored verbatim from user text / model output. There is no
injection risk at the DB layer (ORM-parameterized) and no server-side templating.
Stored-XSS is purely a **frontend rendering** concern — flagged so stage 08 / the
frontend escape output. No backend action required beyond the existing length
bounds.

### L-4 — `claude_parser` creates a new `AsyncClient` per call  (Low)

`parser/claude_parser.py:62` opens a fresh `httpx.AsyncClient` per parse. Fine at
single-user volume; a module-level reusable client would be marginally more
efficient. Not a security issue.

---

## Informational

### I-1 — `PARSE_MAX_INPUT_CHARS` is defined but not enforced in code

`settings.PARSE_MAX_INPUT_CHARS=500` exists but nothing reads it; the actual cap is
the pydantic `TodoParse.text` `max_length=500` (`schemas.py:122`). They agree
today, so input is correctly bounded (good — this is what matters for DoS). But if
an operator raises `PARSE_MAX_INPUT_CHARS` expecting a larger cap, the schema still
rejects at 500 silently. Either wire the setting into the schema or drop the unused
setting to avoid a misleading knob. No security impact (the effective cap is tight).

### I-2 — `Channel` Protocol signature drift (cosmetic)

`channels.py:25` / `delivery.py:25` declare `deliver(self, reminder, *, text)`
(no `session`), but concrete channels and the job call
`deliver(session, reminder, text=text)`. Runtime is fine (duck-typed); the Protocol
is just out of date. Tidy for maintainability.

### I-3 — Retention never prunes `failed` reminders

`clean_fired_reminders` (`service.py:594-611`) deletes only
`sent/acknowledged/expired` past `REMINDER_RETENTION_DAYS`. `failed` rows (and
their deliveries) are retained indefinitely. Given M-1 (job never sets `failed`
today), this is currently moot; if M-1 is fixed, confirm whether `failed` should be
pruned or kept for diagnostics. Unbounded growth is otherwise controlled: todos are
intentionally retained forever (NFR-23), reminders are pruned daily, and the
`(status, fire_at)` index keeps the scan from full-table-scanning as history grows.

---

## What was verified GOOD (accepted / no action)

- **Auth gating (NFR-11, FR-21/22).** `planner_router` applies
  `dependencies=[Depends(get_current_user)]` at the **router** level
  (`router.py:44-48`), so all 9 routes — including the in-app reminder feed and the
  ack endpoint — are gated. `get_current_user` (`auth/jwt.py`) rejects missing
  creds, invalid tokens, and any `sub != ADMIN_USERNAME`; `decode_access_token`
  requires `exp` + `sub` and relies on PyJWT's own expiry check (no hand-rolled
  `<`/`<=` comparison to get wrong). Stage 06 parametrized 401/no-leak across all
  endpoints. No bypass found.
- **Secrets/config.** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY`
  all come from `Settings` (pydantic-settings), never `os.environ`. `.env` is
  gitignored (`.gitignore` has `.env`, `.env.*`, `!.env.example`) and **not
  git-tracked** (verified). `.env.example` contains placeholders only (secrets
  commented out or dummy). No secret appears in any API response. (The one leak is
  H-1, logging only.)
- **No SQL injection.** All DB access is SQLAlchemy ORM / Core with bound params;
  no raw string SQL anywhere in the feature (`grep` confirmed).
- **Input validation.** Datetimes reject naive values and normalize to UTC
  (`schemas._to_utc`); `title` bounded 1–255; `text` bounded 1–500; `lead_time`
  must be positive; list endpoints have bounded `limit` (`ge=1, le=500`) and
  `offset` (`ge=0`); events require `from`/`to` with `to >= from`. No unbounded
  query surface.
- **Exactly-once reminder gate.** The persisted `pending→sending→sent` transition
  with a guarded `UPDATE ... WHERE status='pending'` + `rowcount==1` check is
  correct; stale `sending` re-claim after `REMINDER_CLAIM_GRACE` handles crash
  recovery; catch-up `expired` cutoff honors `REMINDER_CATCHUP_WINDOW`. Durable in
  Postgres (not APScheduler), so restart catch-up works. (M-1/M-2 are the residual
  hardening items, not correctness bugs for v1.)
- **Parse failure isolation.** Parser down/timeout/failed → 503, ambiguous → 422,
  and **nothing is persisted** on either (verified in `parse_and_create` — the DB
  session opens only after a successful extraction+resolution). A failed Telegram
  send does not poison the in-app channel (independent per-channel try/except).
- **Event-loop safety.** Ollama uses the async client; `dateparser` resolution is
  offloaded via `asyncio.to_thread`; the scan is an indexed bounded query
  (`LIMIT 200`) on `(status, fire_at)`. The parse path never blocks the loop.
- **Prompt-injection posture.** The SLM only extracts `title` + rough phrases and
  is explicitly told not to compute dates; all date math is deterministic
  (`dateparser`). Even a malicious/jailbroken model output can at worst produce a
  weird `title`/`date_expr`; the resolver either resolves it to a datetime or
  triggers the ambiguous-422 path. No model output is executed, used in a query, or
  used to make an outbound request — the only hosts contacted are the configured
  Ollama/Anthropic endpoints (NFR-13 holds).

---

## Handoff — to DevOps & Deployment Agent (stage 08)

### BLOCKING (must fix before ship)
- **H-1 (High) — Telegram bot token leaked to logs** (`notify/service.py:43`).
  One-line fix: stop logging the raw httpx exception (which contains the
  token-bearing URL); log only the status code / exception type. **This is the sole
  ship-blocker.**

### DEPLOY-TIME GATE (stage 08 must perform, not a code fix)
- **M-4 — Apply and diff the migration on real Postgres.** Run
  `cd src && uv run alembic upgrade head`, confirm an autogenerate diff is empty,
  and smoke-test one cascading `DELETE /todos/{id}`. DB-level FK cascade / SET NULL
  / CHECK constraints are unverified until this runs.

### NON-BLOCKING (recommended fast-follows; operator may accept the risk)
- **M-1** — Set reminder `sent`/`failed` from actual channel results; don't swallow
  an in-app-channel failure.
- **M-2** — Keep single-worker deployment an invariant; adopt `FOR UPDATE SKIP
  LOCKED` only if multi-worker is ever introduced.
- **M-3** — Add a concurrency cap (semaphore) around `/todos/parse` to protect the
  8GB box and the sampler.
- **L-1/L-2/L-3/L-4, I-1/I-2/I-3** — hardening and tidy-ups; no security impact.

### SIGN-OFF
**Status: BLOCKED — fix H-1 (High) first.** H-1 is a trivial, low-regression-risk
change. Once H-1 is corrected (and the M-4 migration gate is scheduled into the
deploy runbook), the feature is **cleared for stage 08**. No Critical issues were
found; auth, secrets-at-rest, input validation, SQL safety, and the exactly-once
core are all sound.
