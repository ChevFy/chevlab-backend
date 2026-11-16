# Calendar + To-Do — Code Review & Sign-off (Stage 09)

- **Feature slug:** `calendar-todo`
- **Stage:** 09 — Code Review & Documentation (final gate)
- **Status:** SHIP (with a short low-severity punch list, none blocking)
- **Author:** Code Review & Documentation Agent
- **Date:** 2026-11-15
- **Inputs:** `01`–`08` (all APPROVED), `src/modules/planner/**`,
  `src/modules/notify/**`, `src/modules/scheduler/scheduler.py`, `src/main.py`,
  `src/core/config.py`, the Alembic migration, `tests/planner/**`.

> Scope: this stage is a **review** plus documentation. No production logic in
> `src/` was changed. The only code edit is a lint-hygiene `# noqa` on a
> pre-existing `health` handler (below). Repo-wide `ruff format` was applied.

---

## 0. CI gate — first-run green

Stage 08 added `.github/workflows/ci.yml` (ruff check + ruff format --check +
pytest). The repo failed on **pre-existing debt** unrelated to this feature; both
items are now fixed with hygiene-only edits.

1. **`BLE001` at `src/modules/health/router.py:19`** (pre-existing readiness
   probe). A readiness probe legitimately treats *any* DB failure as "not ready",
   so the blind `except Exception` is intentional. Fixed minimally with a scoped,
   justified ignore:

   ```python
   except Exception:  # noqa: BLE001 — readiness probe: any DB failure means "not ready"
       raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db unavailable")
   ```

2. **9 files not `ruff format`-clean** (8 `tests/planner/*` + `07-security-review.md`
   fenced Python). Ran `uv run ruff format .` → 9 reformatted, 62 unchanged. No
   feature logic touched.

Verbatim results after the fixes:

```
$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
71 files already formatted

$ uv run pytest
collected 114 items
... 114 passed, 4 warnings in 1.48s
```

(The 4 warnings are a Starlette deprecation of the `HTTP_422_UNPROCESSABLE_ENTITY`
alias — cosmetic, not from this feature's own code; see L-3.)

---

## 1. Compliance checklist — contract (04) vs. code

### 1.1 Endpoints (all 9 present, methods/paths/auth/status match)

| # | Contract (04 §6) | Implemented (`router.py`) | Auth | Success | Verdict |
|---|---|---|---|---|---|
| 1 | `POST /api/planner/todos` → `TodoCreated` | `post_todo` | router-level `get_current_user` | `201` | OK |
| 2 | `POST /api/planner/todos/parse` → `ParseResult`/`422`/`503` | `post_todo_parse` | gated | `201`/`422`/`503` | OK |
| 3 | `GET /api/planner/todos` (`done,date,limit,offset`) → `TodoList` | `get_todos` | gated | `200` | OK |
| 4 | `GET /api/planner/todos/{id}` → `TodoOut` / `404` | `get_one_todo` | gated | `200`/`404` | OK |
| 5 | `PATCH /api/planner/todos/{id}` → `TodoCreated` / `404`/`422` | `patch_todo` | gated | `200`/`404`/`422` | OK |
| 6 | `DELETE /api/planner/todos/{id}` → `204` / `404` | `delete_one_todo` | gated | `204`/`404` | OK |
| 7 | `GET /api/planner/events` (`from,to` required) → `EventList` | `get_events` | gated | `200`/`422` | OK |
| 8 | `GET /api/planner/reminders` (`limit`) → `ReminderFeed` | `get_reminders` | gated | `200` | OK |
| 9 | `POST /api/planner/reminders/{id}/ack` → `ReminderOut` / `404`/`409` | `post_reminder_ack` | gated | `200`/`404`/`409` | OK |

- **Auth gating:** `planner_router` carries `dependencies=[Depends(get_current_user)]`
  at the router level — every route is private (FR-21/22, AC-7). Confirmed by
  `tests/planner/test_auth_gating.py` (18 cases, each route → 401 unauthenticated).
- **Prefix note (not a drift):** the contract §7 suggested `prefix="/api"` + per-route
  `/planner/...`; the code uses `prefix="/api/planner"` + `/todos`. The **final paths
  are identical** (`/api/planner/todos`, etc.). No wire difference.
- **Parse outcomes A/B/C:** success → `201 ParseResult` with `assumptions`
  (`parse_and_create`); ambiguous → `422` typed `detail` `code="parse_ambiguous"` +
  `candidate` (`ParseAmbiguous`); infra failure → `503` with codes
  `parse_timeout` / `parse_failed` / `parser_unavailable`. **Nothing persisted on
  B/C** (the DB session is only opened *after* resolution succeeds in
  `parse_and_create`). Matches §3.3 exactly. Verified by `test_parse.py`
  (13 cases incl. "persists nothing").
- **Error envelope:** FastAPI default `{"detail": ...}`; plain string for 404/409;
  typed object (with stable `code`) for the parse endpoint. Matches §1.2.
- **Datetime wire format:** request datetimes validated offset-aware and normalized
  to UTC (`schemas._to_utc`; `/events` uses `AwareDatetime` + `.astimezone(UTC)`);
  naive input → 422. `lead_time` is `timedelta` → ISO-8601 duration. Matches §1.4.
  Verified by `test_timezone.py` (9 cases) and `test_schemas.py`.
- **Pagination/filtering:** todos `limit∈[1,500] default 100`, `offset≥0`, order
  `start_at ASC NULLS LAST, id ASC`; events bounded by required `from/to`, order
  `start_at ASC, id ASC`, overlap predicate with `end_at IS NULL` → point; reminders
  `limit∈[1,500]`. Matches §1.5 / §4.1 / §5.2.

### 1.2 Data model (03) vs. `models.py` + migration

| Element (03) | `models.py` | migration `a1b2c3d4e5f6` | Verdict |
|---|---|---|---|
| 4 tables `events/todos/reminders/reminder_deliveries` | all present | created in FK order events→todos→reminders→reminder_deliveries | OK |
| `todos.event_id` FK → events, `ON DELETE SET NULL`, nullable, unique | matches | matches | OK |
| `events` no back-FK (ORM-derived `todo`) | matches | matches | OK |
| `reminders.todo_id` FK CASCADE (NOT NULL); `event_id` FK CASCADE (nullable) | matches | matches | OK |
| `reminders` scan index `(status, fire_at)` + `ix_reminders_todo_id` | matches | matches | OK |
| `lead_time` `sa.Interval` (Postgres INTERVAL) | matches | `sa.Interval()` | OK |
| `reminder_deliveries` `(reminder_id, channel)` unique + in-app feed index | matches | matches | OK |
| All datetimes `DateTime(timezone=True)` (store-UTC) | matches | matches | OK |
| `status`/`lead_source`/`channel` as String + CHECK | matches | matches | OK |
| retention: `clean_fired_reminders(status terminal AND fire_at<cutoff)` daily | `service.clean_fired_reminders` + `scheduler_clean_reminders` | n/a | OK |
| `migrations/env.py` imports `modules.planner.models` | — | present (line 7) | OK |

**Reminder exactly-once (FR-32/33, NFR-18):** the `reminders.status` guarded claim
`pending→sending` (`job._claim`, single-row guarded UPDATE + `rowcount==1`), success
→ `sent`; crash leaves `sending`, re-claimed after `REMINDER_CLAIM_GRACE`
(`_select_due` stale path). Per-channel `(reminder_id, channel)` unique makes
re-dispatch idempotent. The M-1 fix is present: when **no** channel delivers, the row
is **left `sending`** for retry rather than marked `sent` (`job._dispatch_one`). Catch-up
(FR-34): too-late `pending` → `expired`, within window → delivered with `is_late`.
Matches 03 §4.2/4.3 and 02. Verified by `test_reminder_job.py` (11 cases) and
`test_reminder_feed.py` (10 cases).

### 1.3 Security review (07) — H-1 / M-1 fixes confirmed in code

- **H-1 (Telegram token leak in logs):** `notify/service.py` never logs the exception
  object/URL — only `exc.response.status_code` or `type(exc).__name__`. `is_configured`
  gate; secrets not logged (NFR-21). Present.
- **M-1 (reminder falsely marked sent on total delivery failure):** `job._dispatch_one`
  only sets `sent` when `any_success`; otherwise logs and leaves `sending` for retry.
  Present.

---

## 2. Drift & findings

No **blocking** drift. The implementation faithfully realizes the approved 02/03/04
contracts. Findings are low-severity / cosmetic.

| ID | Severity | Finding | Routed to |
|---|---|---|---|
| L-1 | Low | **CHECK-constraint names in the migration don't carry the `ck_<table>_<name>` convention.** `models.py` passes `name="status"` / `"lead_source"` / `"channel"` as the *constraint_name*; through `Base.metadata`'s naming convention the ORM resolves these to `ck_reminders_status`, etc. (as 03 §3.3/3.4 specifies). But the **hand-authored migration** writes `sa.CheckConstraint(..., name="status")` directly into `op.create_table` **without** `op.f()` / the convention, so the **DB** gets constraints literally named `status`, `lead_source`, `channel`. Behaviorally harmless (constraints still enforce; nothing references the names), but the DB names diverge from both the spec and the ORM metadata — a future autogenerate diff could try to "fix" them. | 03/05 (doc-hygiene; fix on next migration touch) |
| L-2 | Low | **`Channel` Protocol signature mismatch.** `delivery.Channel.deliver(self, reminder, *, text)` omits the `session` arg that the concrete `TelegramChannel`/`InAppChannel.deliver(self, session, reminder, *, text)` and the caller (`job.py`) actually use. Runtime is fine (Protocol isn't enforced at call time), but the Protocol is misleading/stale. | 05 (type-hint cleanup) |
| L-3 | Low | **Starlette deprecation warning** for `status.HTTP_422_UNPROCESSABLE_ENTITY` (renamed `..._CONTENT`). Emitted 4× in tests; pre-existing framework churn, not a correctness issue. | housekeeping |
| L-4 | Info | **`_iso_duration` helper in `service.py` duplicates pydantic's `timedelta`→ISO-8601 rendering.** Used only for the human-readable `Assumption.value`; acceptable (it's for display text, not the wire value, which pydantic serializes). | none (acceptable) |
| L-5 | Info | **`reminder_service._lead_specs_from_ins` is imported across module boundary with a leading underscore** (`service._apply_reminders` calls `reminder_service._lead_specs_from_ins`). Works, but the underscore signals "private"; consider promoting to a public name. | 05 (optional) |

### Open-question defaults — all honored

Every `[DEFAULT ACCEPTED]` decision in 04 is realized: never-persist on
ambiguous/failed parse; `INTERVAL` lead time; String+CHECK enums; per-channel
delivery rows; `end_at NULL` = point; date-only → `all_day` + assumption; UTC on
the wire; default 1-day reminder; both channels fixed (OQ-14); unacknowledged-fired
feed (OQ-12); `is_late` on catch-up (OQ-13). The todo→owned-event service cascade
(OQ-2) is implemented in `delete_todo` and the PATCH dated→floating path.

---

## 3. Code-quality observations

- **Structure:** clean module-per-feature slice. The parser subpackage is correctly
  provider-isolated (`base.py` protocol; `ollama_parser` / `claude_parser` behind a
  `factory`; no provider import leaks into router/service — NFR-13). Reminders split
  into `service` (compute), `job` (scan/dispatch), `channels` + `delivery` (fan-out).
- **Imports:** rooted at `src/` (`from core...`, `from modules...`); relative within
  the slice. No `src.` imports, no raw `os.environ` — all config via
  `core.config.settings`. Compliant with repo conventions.
- **Transactions:** every mutation uses `async with SessionLocal()`; todo+event+reminder
  changes commit together (FR-5/16/17). Service helpers take a provided session so the
  caller owns the transaction — correct pattern, mirrors `system/service.py`.
- **Type hints:** thorough; `Mapped[...]` throughout models; dataclasses with `slots`.
- **Error handling:** typed domain exceptions (`TodoNotFound`, `ReminderNotFound`,
  `ReminderNotFired`, `ParseAmbiguous`, parser `*Error`) mapped to HTTP in the thin
  router with `raise ... from None` to suppress chaining — tidy.
- **Off-loop discipline:** `dateparser` resolution runs via `asyncio.to_thread`
  (NFR-6); Telegram send is bounded by a 10 s httpx timeout; the scan is fully awaited
  so the shared 5 s sampler loop is never blocked.
- **Dead code:** none found. `notify.is_configured` is used by the channel path.

---

## 4. Documentation deliverables

- **`README.md`** — added a "Planner (calendar + to-do)" feature bullet, a
  `/api/planner/...` endpoint table, an NL-parse / reminders explainer, the new env
  vars (pointing to `.env.example` and `08-deployment.md`), and the Ollama +
  `alembic upgrade head` run notes. Kept the file's existing voice/structure; linked
  to the deployment doc rather than duplicating it.
- **API reference** — the endpoint table in `README.md` plus the live FastAPI OpenAPI
  at `/docs` (the repo's existing convention — "Interactive API docs are available at
  `/docs`"). No separate `docs/api/` tree is introduced, consistent with the repo.
- **`.env.example`** — already carries all planner/reminder/telegram vars (verified;
  no change needed).
- **This review** — `docs/design/calendar-todo/09-review.md`.

---

## 5. Sign-off

The `calendar-todo` (planner) feature **matches the approved 02/03/04 artifacts**:
all 9 private endpoints, all 4 tables, parse A/B/C outcomes, exactly-once reminder
firing with crash recovery, and the H-1/M-1 security fixes are present in code. CI
is green on first run (114 tests pass; ruff check + format clean). The only findings
are five low-severity / informational items (L-1…L-5), none blocking; L-1 and L-2
are routed back to stage 05 for a cosmetic cleanup on the next migration/typing touch.

**Recommendation: SHIP.**

---

## Handoff

- **Compliance verdict:** PASS. No blocking drift. Implementation conforms to the
  approved data model (03) and API contract (04); security fixes (07 H-1, M-1)
  verified in code.
- **Outstanding issues (non-blocking):**
  - L-1 → stage 05/03: align migration CHECK-constraint names with the
    `ck_<table>_<name>` convention next time the migration is regenerated.
  - L-2 → stage 05: fix the `Channel` Protocol `deliver` signature (add `session`).
  - L-3 → housekeeping: migrate off the deprecated `HTTP_422_UNPROCESSABLE_ENTITY`
    alias when convenient.
- **Doc deliverables:** `README.md` (updated), FastAPI `/docs` OpenAPI reference,
  `.env.example` (verified current), `docs/design/calendar-todo/09-review.md` (this).
- **CI:** green — `ruff check` clean, `ruff format --check` clean, `pytest` 114 passed.
- **Final recommendation: SHIP.** Pipeline closed for `calendar-todo`.
