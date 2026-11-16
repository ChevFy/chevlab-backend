# Calendar + To-Do — Test & QA Report (Stage 06)

- **Feature slug:** `calendar-todo`
- **Stage:** 06 — Testing & QA
- **Date:** 2026-11-15
- **Inputs:** `01-requirements.md` (AC-1..14), `04-api-contract.md` (9 endpoints),
  `05-implementation-notes.md` (hot spots), implemented `src/modules/planner/`.

## Final result (verbatim)

```
configfile: pytest.ini
testpaths: tests
plugins: asyncio-1.5.0, anyio-4.15.1
asyncio: mode=Mode.AUTO
collected 114 items

tests/planner/test_auth_gating.py ..................                     [ 15%]
tests/planner/test_e2e_default_reminder.py ...                           [ 18%]
tests/planner/test_events.py ..........                                  [ 27%]
tests/planner/test_parse.py .............                                [ 38%]
tests/planner/test_reminder_feed.py ..........                           [ 47%]
tests/planner/test_reminder_job.py ...........                           [ 57%]
tests/planner/test_schemas.py ..........                                 [ 65%]
tests/planner/test_timezone.py .........                                 [ 73%]
tests/planner/test_todos_crud.py ..................                      [ 89%]
tests/planner/test_update_lifecycle.py ...........                       [ 99%]
tests/test_smoke.py .                                                    [100%]

======================= 114 passed, 4 warnings in 1.25s ========================
```

**114 passed, 0 failed.** The 4 warnings are a pre-existing Starlette
deprecation (`HTTP_422_UNPROCESSABLE_ENTITY` → `..._CONTENT`) emitted from
FastAPI internals, not from feature code.

## Test environment (no Postgres/Docker in sandbox)

Per the stage-05 handoff, no Postgres/Docker is reachable here, so the suite runs
against an in-memory async **SQLite (aiosqlite)** DB. Tables are created from the
SQLAlchemy metadata (`Base.metadata.create_all`), **not** Alembic. Three
SQLite↔Postgres gaps are bridged **test-side only** (in `tests/conftest.py`), with
**zero changes to production behaviour**:

1. `BigInteger` PKs do not autoincrement in SQLite → a dialect-level `@compiles`
   hook emits `INTEGER` so `INTEGER PRIMARY KEY` autoincrements (mirrors
   Postgres `BIGSERIAL`).
2. SQLite strips tzinfo from `DateTime(timezone=True)` on read → a colspec
   override returns every datetime UTC-aware (ORM *and* bare-column selects),
   matching Postgres `timestamptz`. This is required for the firing job and the
   reminder-feed `is_late` math, which mix an aware `now` with DB values.
3. `INTERVAL` has no SQLite equivalent → a seconds-as-float `TypeDecorator` is
   substituted for `Interval` on SQLite, round-tripping `timedelta` cleanly.

The parser is **always stubbed** (`tests/planner/stubs.py` → `StubParser`) so
Ollama is never contacted; the deterministic `dateparser` resolver runs for real
(so timezone resolution is genuinely exercised). `notify.send_message` (Telegram)
is always monkeypatched — **no network I/O**. The module-level `SessionLocal` is
monkeypatched into `core.database`, `planner.service`, and `reminders.job`.

## What was tested — coverage by acceptance criterion

| AC | Covered by | Notes |
|---|---|---|
| AC-1 NL capture w/ time | `test_parse.py::test_parse_success_dated_*` | stubbed SLM + real resolver; event + reminder created |
| AC-2 floating task | `test_parse.py::test_parse_success_floating_has_assumption`, `test_todos_crud` | event null, assumption surfaced |
| AC-3 parse failure recoverable | `test_parse.py` outcome-C (503 x3) + "persists nothing" | nothing persisted on 503/422 |
| AC-4 structured create bypasses parser | `test_todos_crud.py::test_create_*` | no SLM call on `/todos` |
| AC-5 filtered list | `test_todos_crud.py::test_list_filter_*`, `test_timezone::test_todos_date_filter_*` | done + Bangkok-day filters |
| AC-6 calendar range overlap | `test_events.py` (overlap, spanning, ordering, null-end point) | FR-20 overlap incl. before-window start |
| AC-7 auth gating | `test_auth_gating.py` (all 9 routes, 401 + no leak) | parametrized over every endpoint |
| AC-8 timezone correctness | `test_timezone.py` (`tomorrow 9am`→02:00Z, `15:00`→08:00Z) | real resolver, UTC on the wire |
| AC-9 default reminder fires once both channels | `test_e2e_default_reminder.py::test_ac9_*`, `test_reminder_job.py` | create→fire, exactly-once, both channels |
| AC-10 reminder lead from NL | `test_parse.py::test_parse_success_dated_with_parsed_reminder` | `lead_source=parsed`, PT2H |
| AC-11 configurable default | `test_e2e_default_reminder.py::test_ac11_*` | settings override → new lead, no code change |
| AC-12 floating has no reminder | `test_e2e_default_reminder.py::test_ac12_*`, `test_update_lifecycle` | no reminder scheduled/fired |
| AC-13 survives restart, no loss/dup | `test_reminder_job.py::test_survives_restart_fires_once`, `test_concurrent_scans_fire_once` | persisted state + guarded claim |
| AC-14 missed-reminder catch-up | `test_reminder_job.py::test_within_catchup_window_delivered_as_late`, `test_too_late_*_expires` | window cutoff + late flag |

### Stage-05 hot spots (05-notes) — all exercised

1. **Exactly-once firing** (`reminders/job.py`): guarded `pending→sending→sent`
   claim, stale-`sending` re-claim after `REMINDER_CLAIM_GRACE`, fresh-`sending`
   skipped, catch-up `expired` cutoff, repeated scans don't re-fire, **concurrent
   scans dispatch exactly once** (`asyncio.gather`), restart survival. →
   `test_reminder_job.py` (11 tests, all green).
2. **Parse outcomes A/B/C** with nothing-persisted assertions on B/C. →
   `test_parse.py` (13 tests).
3. **Todo link lifecycle in `update_todo`**: floating→dated, dated→floating
   (event deleted + reminders cleared, todo survives), due-time change (pending
   recomputed, **sent untouched**), explicit `[]` clears, replace set. →
   `test_update_lifecycle.py` (11 tests).
4. **Timezone** (AC-8): resolver unit tests + events/todos UTC-window translation.
   → `test_timezone.py` (9 tests).
5. **Auth gating** on all 9 routes. → `test_auth_gating.py` (18 assertions).
6. **Graceful degradation**: Telegram down → in-app still recorded + reminder
   `sent`; one channel raising doesn't block the other; parser down → CRUD
   unaffected. → `test_reminder_job.py`, `test_parse.py`.
7. **Default reminder** 1-day, settings-configurable, both channels. →
   `test_e2e_default_reminder.py`.

## Bugs found and fixed

Two **real implementation bugs** were found by the tests and fixed with minimal,
behaviour-preserving changes. Both would also manifest on Postgres (they are not
SQLite artifacts).

### BUG-1 — `update_todo` crashes (500) when `reminders` are supplied

`update_todo` does `fields = body.model_dump(exclude_unset=True)`, which turns the
nested `reminders` into a list of plain **dicts**. It then passed them to
`_apply_reminders`, which is shared with the create path and expects
`ReminderIn` objects, so:

```
AttributeError: 'dict' object has no attribute 'lead_time'
  src/modules/planner/service.py:106  (lead_deltas = [r.lead_time for r in reminders_in])
```

Impact: any `PATCH /todos/{id}` that sets explicit `reminders` (floating→dated
with reminders, or an explicit replace) returned a 500. The create path was
unaffected (it passes `ReminderIn` objects directly).

**Fix** (`src/modules/planner/service.py`, `update_todo`): re-wrap the dumped
dicts into `ReminderIn` before use:

```python
raw_reminders = fields.get("reminders")
reminders_in = (
    [schemas.ReminderIn.model_validate(r) for r in raw_reminders]
    if raw_reminders is not None
    else None
)
```

### BUG-2 — first-ever reminder delivery silently fails (no notification sent)

`_get_or_create_delivery` creates a new `ReminderDelivery` without a Python-side
`attempts` value (the column only had `server_default="0"`, which fires at INSERT,
not at object construction). The very next line `delivery.attempts += 1` runs
**before** any flush, so:

```
TypeError: unsupported operand type(s) for +=: 'NoneType' and 'int'
  src/modules/planner/reminders/channels.py:54  (delivery.attempts += 1)
```

Impact (severe): the exception is raised inside each channel's `deliver`, which
the job catches and only logs (`except Exception: logger.exception(...)`), then
still marks the reminder `sent`. So on the **first** dispatch of every reminder,
**no delivery row is written and no Telegram/in-app message is recorded** — the
operator is never notified, yet the reminder is marked done and never retried.
This defeats AC-9/AC-14 and NFR-22 in production. (Re-claim/stale paths that hit
an already-flushed delivery row were unaffected.)

**Fix** (`src/modules/planner/reminders/channels.py`): set `attempts=0` on
construction, and add a Python-side `default=0` on the model column for defence in
depth (`src/modules/planner/models.py`). After the fix, both channels record
deliveries on the first dispatch and the in-app feed surfaces the reminder.

Both fixes are covered by regression tests that were red before and green after:
`test_update_lifecycle.py::test_floating_to_dated_with_explicit_reminders`,
`::test_explicit_reminders_replace_set` (BUG-1);
`test_reminder_job.py::test_due_reminder_fires_once_both_channels` and the rest of
that file (BUG-2).

## Coverage notes / gaps

- **Parser implementations** (`ollama_parser.py`, `claude_parser.py`) are NOT unit
  tested — they require Ollama / an Anthropic key and are intentionally stubbed
  behind the `Parser` protocol. Their contract (raising the typed
  `ParserUnavailable/ParseTimeout/ParseFailed`, one call per parse) is exercised
  at the service boundary via `StubParser`. Hand to stage 07 to consider mocking
  the `ollama`/`httpx` clients for the real impls.
- **Thai colloquial phrases** (`3 โมงเย็น` etc.) that `dateparser` can't resolve
  in one shot (05-notes deviation #3) are exercised only through the ambiguity
  path (422 + candidate), using a stubbed clean phrase. Real Thai SLM accuracy
  (OQ-10) is out of scope for automated tests and needs a human/eval harness.
- **APScheduler wiring** (`scheduler.py` / lifespan) is not started in tests (it
  would hit real Postgres and run the metrics sampler). The firing function
  `scan_due_reminders` is tested directly; the *registration* (interval, catch-up
  on first run) is verified only by reading the code.
- **Migration** (`a1b2c3d4e5f6_*`) could not be applied (no DB). Tables were built
  from `Base.metadata.create_all`, so the suite validates the **models**, not the
  hand-authored migration DDL. **Deploy must run `cd src && uv run alembic upgrade
  head` against the real Postgres and confirm it matches the models** (stage-05
  deviation #1). Index/constraint names and the DB-level `ON DELETE CASCADE`
  behaviour are therefore *unverified against a live DB*.
- **DB-level CASCADE** (`reminders.todo_id`, `reminder_deliveries.reminder_id`)
  relies on Postgres FK `ON DELETE CASCADE`; SQLite does not enforce FKs by
  default, so delete-cascade here is validated at the **service/ORM** layer
  (`delete-orphan` + explicit deletes), not the DB-FK layer. Re-verify on Postgres.
- **Concurrency** is tested via cooperative `asyncio.gather` on one event loop
  (the production model — single `api` process, async scheduler). True
  multi-connection row-lock races (`SELECT ... FOR UPDATE SKIP LOCKED`-style) are
  not reproducible on in-memory SQLite; the guarded `UPDATE ... WHERE status=...`
  gate is the correctness mechanism and is asserted to yield exactly one winner.

## New dev dependencies added

`pytest-asyncio` and `aiosqlite` were added to the `dev` group (`uv add --dev`)
so the async service/DB layer can be tested without Postgres.

## Handoff

**Test status:** 114 passed / 0 failed. The suite is green.

**Bugs found (both FIXED in this stage, minimal changes):**
- BUG-1 — `update_todo` 500 on explicit `reminders` (dict vs `ReminderIn`).
- BUG-2 — first reminder dispatch silently dropped (uninitialised `attempts`);
  **high-severity** (operator never notified yet reminder marked sent).

Stage 05, please review both fixes
(`src/modules/planner/service.py`, `reminders/channels.py`, `models.py`) — they
are regression-covered but you own the production code.

**Risk areas for the Security & Reliability Agent (stage 07) to probe:**
- Real `ollama_parser` / `claude_parser` behaviour and the NFR-13 "no arbitrary
  outbound request" guarantee (only the configured host is contacted); bounded
  timeout enforcement (`PARSE_TIMEOUT_S`) under a hanging model.
- NFR-22 Telegram failure/retry policy under the real `httpx` path (tested only
  via stub true/false/raise).
- The hand-authored migration vs. the live Postgres schema (names, CHECK
  constraints, DB-level `ON DELETE CASCADE`) — **must** be applied and diffed.
- Exactly-once under genuine multi-connection contention on Postgres (the
  `pending→sending` guarded UPDATE); verify no `SELECT`-then-`UPDATE` TOCTOU on
  the real engine with concurrent scheduler ticks.
- Parse endpoint input-abuse surface (500-char cap enforced; confirm no
  ReDoS/large-payload amplification in `dateparser`).

**State: ready for stage 07.** (No blocking failures; the two bugs are fixed and
regression-tested. Deploy-time Alembic application remains an environment-limited
verification gap to close against real Postgres.)
```
