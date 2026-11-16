# Calendar + To-Do — API & Contract (Stage 04)

- **Feature slug:** `calendar-todo`
- **Stage:** 04 — API & Contract
- **Status:** Draft — awaiting approval
- **Author:** API & Contract Agent
- **Date:** 2026-11-15
- **Inputs:** `01-requirements.md` (APPROVED), `02-architecture.md` (APPROVED),
  `03-data-model.md` (APPROVED)

> Scope note: this document specifies the **exact HTTP wire contract** — paths,
> methods, auth gating, request/response pydantic v2 schemas, status codes, the
> shared error envelope, and filtering/pagination. It does **not** implement any
> code under `src/` (stage 05). Schema snippets are the *design target* stage 05
> authors verbatim in `src/modules/planner/schemas.py`.

> **Auto-proceed note:** the operator authorized running stages 04→05→06 straight
> through without a review gate, accepting the stage-03-recommended default for
> every open question. Each such choice is marked **[DEFAULT ACCEPTED]** inline.

---

## 0. Locked decisions applied here

From the task's locked decisions and stage 03 §0, this contract is built on:

1. **Module folder `src/modules/planner/`**, routes mount under `/api` →
   **all endpoints are `/api/planner/...`**. (Overrides stage 02's `calendar/`.)
2. **Auth:** every endpoint is **private**, gated by the existing
   `get_current_user` (JWT bearer, HS256, `sub == ADMIN_USERNAME`) from
   `src/modules/auth/jwt.py`. Unauthenticated/invalid → `401`.
3. **UTC-on-the-wire** for all datetimes. The API emits/accepts UTC ISO-8601
   (`...Z` / `+00:00`). Asia/Bangkok is only the *resolution* timezone inside the
   parser (stage 05), never the wire format.
4. Stage-03 defaults accepted: service-level todo→owned-event cascade on delete;
   `lead_time` as `INTERVAL` (wire: ISO-8601 duration string); `status`/`channel`
   as `String`+CHECK; per-channel delivery rows; `events.end_at` NULL = point.

### 0.1 Resource layout (the 4-table model → URL tree)

Aligned to the four tables (`todos`, `events`, `reminders`, `reminder_deliveries`),
a clean, consistent, resource-oriented layout:

| Resource | Base path | Backed by |
|---|---|---|
| To-dos | `/api/planner/todos` | `todos` |
| — NL capture | `/api/planner/todos/parse` | `todos` (+ `events`, `reminders`) |
| Events (calendar) | `/api/planner/events` | `events` |
| Reminders (in-app feed + ack) | `/api/planner/reminders` | `reminders` + `reminder_deliveries` |

`reminder_deliveries` is **not** its own public resource — it is internal
delivery state that surfaces *through* the reminders feed (the in-app channel's
rows become the poll feed; FR-31a). Telegram delivery is one-way push built in
stage 05 (`notify`); it is never an HTTP input here.

**Create input is web-only.** No Telegram-input endpoint exists.

---

## 1. Conventions

### 1.1 Auth gating (all routes private)

Every route carries `dependencies=[Depends(get_current_user)]` (matching
`modules/docker/router.py`). There are **no public endpoints** in this feature
(FR-21/22). An unauthenticated or invalid-token request is rejected with `401`
and the standard error envelope **before** any handler logic runs; no data is
returned (AC-7).

### 1.2 Error envelope

The repo uses FastAPI's default error shape (`HTTPException(detail=...)` in
`auth`/`docker` routers). This contract **standardizes on that same envelope** —
no custom middleware, no new wrapper — so it is consistent with the rest of
chevlab-backend:

```json
{ "detail": "human-readable message" }
```

- For **validation errors** FastAPI/pydantic emit the framework default `422`
  with a structured `detail` array:

```json
{
  "detail": [
    {
      "type": "string_too_long",
      "loc": ["body", "text"],
      "msg": "String should have at most 500 characters",
      "input": "...",
      "ctx": { "max_length": 500 }
    }
  ]
}
```

- For **domain errors raised by handlers** (not found, parser down, ambiguous
  parse), `detail` is either a plain string **or** a typed object. The
  **parse endpoint is the one exception** that returns a richer typed `detail`
  object (an error/clarification shape, §3.3) so the frontend can drive the
  recover-or-confirm UX (FR-8/9/10). Its `detail` object always carries a
  stable `code` field.

**Typed error `detail` object** (used by the parse endpoint and any domain
error that needs a machine-readable code):

```python
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str  # stable machine code, e.g. "parser_unavailable"
    message: str  # human-readable
    # optional extras per error kind:
    assumptions: list["Assumption"] | None = None
    candidate: "ParseCandidate | None" = None
```

### 1.3 Status-code conventions

| Code | When |
|---|---|
| `200 OK` | Successful GET, PATCH, successful parse-and-create, ack/complete. |
| `201 Created` | Successful `POST /todos` and `POST /todos/parse` that persist. |
| `204 No Content` | Successful `DELETE`. |
| `401 Unauthorized` | Missing/invalid JWT (all routes). |
| `404 Not Found` | Target `id` does not exist (todo / reminder). |
| `409 Conflict` | State conflict (e.g. ack-ing an already-acknowledged reminder — idempotent-safe, see §5.3). |
| `422 Unprocessable Entity` | Request body/query fails pydantic validation, **and** the parse "ambiguous / unresolved, please clarify" case (§3.3). |
| `503 Service Unavailable` | Parser (Ollama/cloud) down or unconfigured (`/parse` only; CRUD stays up — NFR-7/14). |

> **Note — parse uses both `201` and `422`/`503`:** a *successful* parse creates
> and returns `201`; an *ambiguous/unresolvable* parse that we refuse to persist
> returns `422` with a clarification `detail`; a *parser-infrastructure failure*
> returns `503`. This is the explicit resolution of OQ-1 (ambiguity contract),
> detailed in §3.3. **[DEFAULT ACCEPTED]** (stage-03/01 recommended: never persist
> a malformed todo; return a structured recoverable error).

### 1.4 Datetime & duration wire format

- **Datetimes:** UTC ISO-8601, e.g. `2026-11-16T08:00:00Z`. Requests that include
  a timezone offset are normalized to UTC; a naive datetime in a request body is
  **rejected** (`422`) — the client must send an explicit offset so there is no
  ambiguity (NFR-9/10).
- **`lead_time`:** ISO-8601 duration string on the wire (e.g. `"P1D"`, `"PT2H"`),
  mapping to Postgres `INTERVAL` / Python `timedelta`. **[DEFAULT ACCEPTED]**
  (stage-03 §8.5: `INTERVAL`).

### 1.5 Pagination & filtering

- **To-dos list:** filter-first (single-user, small volume). Filters: `done`,
  `date`, plus **cursor-free offset pagination** (`limit`, `offset`) with a
  bounded `limit` (default 100, max 500). Deterministic order
  `start_at ASC NULLS LAST, id ASC`.
- **Events list:** bounded by the mandatory `from`/`to` window — no pagination
  (the window *is* the bound); order `start_at ASC, id ASC`.
- **Reminders feed:** returns the unacknowledged fired set (small); supports an
  optional `limit` (default 100, max 500). No cursor needed at single-user scale.

---

## 2. Shared schemas

All schemas are pydantic v2 `BaseModel`, matching the repo style
(`auth/router.py` defines inline `BaseModel`s; this feature groups them in
`src/modules/planner/schemas.py`). `model_config = ConfigDict(from_attributes=True)`
on response models so they serialize directly from ORM rows.

```python
from datetime import datetime, timedelta
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


# ---------- Response resources ----------


class TodoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    source_text: str | None = None  # raw NL text (parse path); null for structured
    start_at: datetime | None = None  # UTC; null = floating
    end_at: datetime | None = None  # UTC
    all_day: bool
    done: bool
    completed_at: datetime | None = None  # UTC
    event_id: int | None = None  # null = floating
    created_at: datetime
    updated_at: datetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    start_at: datetime  # UTC; events always dated
    end_at: datetime | None = None  # UTC; null = point (OQ-3 default)
    all_day: bool
    todo_id: int | None = None  # derived source todo (from todos.event_id)
    created_at: datetime
    updated_at: datetime


class ReminderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    todo_id: int
    event_id: int | None = None
    lead_time: timedelta  # serialized as ISO-8601 duration, e.g. "P1D"
    lead_source: Literal["default", "parsed", "manual"]
    fire_at: datetime  # UTC; when it fires / fired
    status: Literal["pending", "sending", "sent", "failed", "expired", "acknowledged"]
    sent_at: datetime | None = None
    acknowledged_at: datetime | None = None


# ---------- Parse-support value objects ----------


class Assumption(BaseModel):
    """A default the system filled, surfaced to the operator (FR-9)."""

    field: str  # e.g. "all_day", "start_at", "lead_time"
    value: str  # stringified assumed value
    reason: str  # e.g. "no time given → treated as all-day"


class ReminderIn(BaseModel):
    """A reminder lead-time on create/update (structured path, FR-28)."""

    lead_time: timedelta  # ISO-8601 duration, e.g. "PT2H"
```

**`lead_time` serialization:** pydantic v2 renders `timedelta` as an ISO-8601
duration string (e.g. `"P1D"`, `"PT2H"`) and parses the same on input — this is
the wire form for the `INTERVAL` column. Stage 05 keeps this default and does not
hand-roll a seconds-int format.

---

## 3. To-dos

### 3.1 `POST /api/planner/todos` — structured create (no parser)

- **Private.** FR-11 / AC-4. **No SLM call is ever issued on this path.**
- Creates a todo; if `start_at` is provided, the service also creates+links an
  `Event` and schedules reminder(s) in the same transaction (FR-5/17, FR-24).

**Request body** `TodoCreate`:

```python
class TodoCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    start_at: datetime | None = None  # UTC (offset required if present)
    end_at: datetime | None = None  # UTC; must be >= start_at if both set
    all_day: bool = False
    reminders: list[ReminderIn] | None = None  # explicit lead times (FR-28)
```

Validation rules (pydantic `model_validator`, stage 05):
- `end_at >= start_at` when both present → else `422`.
- `reminders` requires `start_at` (a floating todo cannot have reminders,
  FR-26) → else `422`.
- `reminders is None` **and** `start_at` present → service applies the configured
  **default 1-day** reminder (`lead_source="default"`, FR-24/25). Passing an empty
  list `[]` explicitly means **no reminders** (allows clearing, FR-28).

**Response** `201 Created`, body `TodoCreated`:

```python
class TodoCreated(BaseModel):
    todo: TodoOut
    event: EventOut | None = None  # present iff dated
    reminders: list[ReminderOut] = []  # scheduled reminders (may be empty)
```

Example (dated, default reminder applied):

```json
{
  "todo": {
    "id": 42, "title": "Pay rent", "source_text": null,
    "start_at": "2026-12-01T03:00:00Z", "end_at": null,
    "all_day": false, "done": false, "completed_at": null,
    "event_id": 17, "created_at": "2026-11-15T09:12:00Z",
    "updated_at": "2026-11-15T09:12:00Z"
  },
  "event": {
    "id": 17, "title": "Pay rent",
    "start_at": "2026-12-01T03:00:00Z", "end_at": null,
    "all_day": false, "todo_id": 42,
    "created_at": "2026-11-15T09:12:00Z", "updated_at": "2026-11-15T09:12:00Z"
  },
  "reminders": [
    {
      "id": 88, "todo_id": 42, "event_id": 17,
      "lead_time": "P1D", "lead_source": "default",
      "fire_at": "2026-11-30T03:00:00Z", "status": "pending",
      "sent_at": null, "acknowledged_at": null
    }
  ]
}
```

**Errors:** `401`, `422` (validation).

### 3.2 `GET /api/planner/todos` — list with filters

- **Private.** FR-12 / AC-5. Served by `ix_todos_done_start_at`.

**Query params:**

| Param | Type | Default | Notes |
|---|---|---|---|
| `done` | `bool \| None` | `None` | omit = all; `true`/`false` filters completion. |
| `date` | `date \| None` | `None` | calendar date (operator-local, Asia/Bangkok); matches todos whose `start_at` falls on that local day (translated to a UTC `[day_start, day_end)` window, NFR-10). Floating todos are excluded when `date` is set. |
| `limit` | `int` | `100` | `ge=1, le=500`. |
| `offset` | `int` | `0` | `ge=0`. |

> **`date` is interpreted in Asia/Bangkok** (the operator's calendar day), then
> converted to a UTC half-open window for the query — consistent with resolving
> input in Bangkok while storing UTC. **[DEFAULT ACCEPTED]**

**Response** `200 OK`, body `TodoList`:

```python
class TodoList(BaseModel):
    items: list[TodoOut]
    total: int  # total matching (for pagination UI)
    limit: int
    offset: int
```

**Errors:** `401`, `422` (bad query).

### 3.3 `POST /api/planner/todos/parse` — NL capture (SLM + dateparser)

- **Private.** FR-1..FR-10, FR-27, AC-1/2/3/10. This is the headline endpoint.
- Flow (stage 02 §3a): enforce max input length → one SLM call (off event loop,
  bounded timeout) extracting `title` + rough `date_expr` + rough `reminder_expr`
  → `dateparser` resolves to concrete Asia/Bangkok datetimes (stored UTC) → on
  success create todo (+event if dated, +reminders incl. default 1-day) → return
  created resources **with visible assumptions**.

**Request body** `TodoParse`:

```python
class TodoParse(BaseModel):
    text: str = Field(min_length=1, max_length=500)  # PARSE_MAX_INPUT_CHARS (NFR-8)
```

> `max_length=500` is the **[DEFAULT ACCEPTED]** input cap for OQ-5/NFR-8; the
> enforced value is also bounded by the `PARSE_MAX_INPUT_CHARS` setting, so the
> schema cap and the setting agree (stage 05 wires the setting; `500` is the
> shipped default and the schema's hard max).

#### Outcome A — success (persisted) → `201 Created`

Body `ParseResult`:

```python
class ParseResult(BaseModel):
    todo: TodoOut
    event: EventOut | None = None
    reminders: list[ReminderOut] = []
    assumptions: list[Assumption] = []  # defaults the system filled (FR-9)
```

Notes:
- Dated input (AC-1/10): `event` present, `reminders` non-empty (parsed lead time
  with `lead_source="parsed"`, else default `lead_source="default"`).
- Floating input (AC-2): `event` is `null`, `reminders` is `[]`, and an
  `Assumption` entry explains it is an undated/floating task.
- When a time is assumed (e.g. date-only input treated as `all_day`), an
  `Assumption` row records it (OQ-4 default: date-only → `all_day` task).
  **[DEFAULT ACCEPTED]**

Example (AC-10, `"ประชุมพรุ่งนี้บ่าย 3 โมง เตือนก่อน 2 ชั่วโมง"`, today
2026-11-15):

```json
{
  "todo": {
    "id": 43, "title": "ประชุม", "source_text": "ประชุมพรุ่งนี้บ่าย 3 โมง เตือนก่อน 2 ชั่วโมง",
    "start_at": "2026-11-16T08:00:00Z", "end_at": null,
    "all_day": false, "done": false, "completed_at": null,
    "event_id": 18, "created_at": "2026-11-15T10:00:00Z", "updated_at": "2026-11-15T10:00:00Z"
  },
  "event": {
    "id": 18, "title": "ประชุม",
    "start_at": "2026-11-16T08:00:00Z", "end_at": null, "all_day": false,
    "todo_id": 43, "created_at": "2026-11-15T10:00:00Z", "updated_at": "2026-11-15T10:00:00Z"
  },
  "reminders": [
    {
      "id": 90, "todo_id": 43, "event_id": 18,
      "lead_time": "PT2H", "lead_source": "parsed",
      "fire_at": "2026-11-16T06:00:00Z", "status": "pending",
      "sent_at": null, "acknowledged_at": null
    }
  ],
  "assumptions": []
}
```

> (`บ่าย 3 โมง` = 15:00 Asia/Bangkok = `08:00Z`; reminder 2h before = `06:00Z`.)

#### Outcome B — ambiguous / unresolvable, NOT persisted → `422`

When the SLM returns a title but the date/reminder expression **cannot be
confidently resolved** (`date_expr` present but `dateparser` fails to resolve it,
FR-9a; or multiple plausible interpretations), the system **does not persist a
malformed todo**. It returns `422` with a typed `detail` carrying a *candidate*
the frontend can confirm or correct (two-step confirm-then-create flow — the
frontend re-submits via `POST /api/planner/todos` structured create with the
corrected fields). **[DEFAULT ACCEPTED]** (resolves OQ-1 / stage-01 open question:
return a recoverable error with the candidate; never silently guess, never
persist).

```python
class ParseCandidate(BaseModel):
    title: str | None = None
    date_expr: str | None = None  # the raw phrase the SLM extracted
    reminder_expr: str | None = None
    # resolver's best (unconfirmed) attempt, if any:
    start_at: datetime | None = None
    end_at: datetime | None = None
    all_day: bool | None = None
```

`detail` object (an `ErrorDetail`, §1.2) for this case:

```json
{
  "detail": {
    "code": "parse_ambiguous",
    "message": "Could not confidently resolve the date/time. Please confirm or edit.",
    "candidate": {
      "title": "ประชุม",
      "date_expr": "สัปดาห์หน้า",
      "reminder_expr": null,
      "start_at": null, "end_at": null, "all_day": null
    },
    "assumptions": null
  }
}
```

#### Outcome C — parse failed / parser unavailable, NOT persisted → `503`

The SLM is unavailable, not configured, times out, errors, or returns no usable
structure (FR-10, AC-3, NFR-7/14). Nothing is persisted; the operator can retry
or fall back to structured create.

```json
{ "detail": { "code": "parser_unavailable",
              "message": "The natural-language parser is unavailable. Use structured create or retry." } }
```

Codes for outcome C: `parser_unavailable` (down / unconfigured),
`parse_timeout` (NFR-2 bounded timeout exceeded), `parse_failed` (ran but
produced no usable structure). All three return `503` with no side effects.

**Errors summary for `/todos/parse`:** `401`; `422` (input validation **or**
ambiguous outcome B); `503` (outcome C). **`201`** only on outcome A.

### 3.4 `GET /api/planner/todos/{todo_id}` — fetch one

- **Private.** **Response** `200 OK`, body `TodoOut`.
- **Errors:** `401`, `404` (`{"detail": "Todo not found"}`).

### 3.5 `PATCH /api/planner/todos/{todo_id}` — update

- **Private.** FR-13/14/17/29. Partial update; all fields optional. Omitted
  fields are untouched; a field explicitly set to `null` clears it (distinguished
  via pydantic "unset" vs. `None` — stage 05 uses `model_dump(exclude_unset=True)`).

**Request body** `TodoUpdate`:

```python
class TodoUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    start_at: datetime | None = None  # set → (re)date; explicit null → make floating
    end_at: datetime | None = None
    all_day: bool | None = None
    done: bool | None = None  # true → complete (sets completed_at); false → reopen
    reminders: list[ReminderIn] | None = None  # replace the reminder set; [] clears
```

Service-side link lifecycle (FR-16/17/29, stage 05):
- Floating → dated (`start_at` set): create+link `Event`, schedule reminders
  (explicit `reminders`, else default). 
- Dated → floating (`start_at` set to `null`): **[DEFAULT ACCEPTED]** service
  **deletes the owned event** (cascade) and clears pending reminders; the todo
  survives (FR-16/17).
- Due-time change (`start_at` moved): recompute **pending** reminders'
  `fire_at = new_due - lead_time`; already `sent`/`acknowledged` reminders are
  untouched (FR-29).
- `done=true`: set `completed_at = now()`; `done=false`: clear it (FR-14).

Validation: `end_at >= start_at` if both resolve; `reminders` non-empty requires
the todo to be (or become) dated, else `422` (FR-26).

**Response** `200 OK`, body `TodoCreated` (same shape as create: the todo plus its
current event and reminder set, so the frontend re-renders in one round trip).

**Errors:** `401`, `404`, `422`.

### 3.6 `DELETE /api/planner/todos/{todo_id}` — delete

- **Private.** FR-15/16. **[DEFAULT ACCEPTED]** service-level cascade: deletes the
  todo, its **owned event** (same transaction — the FK points todo→event so this
  is enforced in code, stage-03 §4.1 recommendation), and its reminders (DB
  `ON DELETE CASCADE`), which cascades their delivery rows.
- **Response** `204 No Content` (empty body).
- **Errors:** `401`, `404`.

### 3.7 Complete / reopen convenience (covered by PATCH)

Marking done/undone is done via `PATCH {"done": true|false}` (§3.5). **No separate
`/complete` sub-resource is added** — one update path keeps the surface minimal
and matches FR-13/14 ("at minimum … done status" via update). This is the chosen
clean layout; stage 05 should not add a second completion endpoint.

---

## 4. Events (calendar grid)

### 4.1 `GET /api/planner/events` — range query

- **Private.** FR-18/20, AC-6. Overlap semantics; served by `ix_events_start_end`.

**Query params:**

| Param | Type | Required | Notes |
|---|---|---|---|
| `from` | `datetime` | **yes** | UTC (offset required). Window start (inclusive). |
| `to` | `datetime` | **yes** | UTC. Window end (inclusive). Must be `>= from`. |

Overlap predicate (stage 05): return events where
`start_at <= :to AND (end_at >= :from OR end_at IS NULL)` — i.e. any event whose
span overlaps `[from, to]`, **not only** those starting inside it (FR-20). A
null-`end_at` event is treated as a **point** at `start_at` (OQ-3 default
`end_at := start_at` in the comparison). **[DEFAULT ACCEPTED]** Ordered
`start_at ASC, id ASC` for rendering.

> The frontend computes `from`/`to` from the visible grid (day/week/month) in its
> local timezone and sends UTC. A one-day pad on each side is a frontend concern;
> the backend honors exactly the window given.

**Response** `200 OK`, body `EventList`:

```python
class EventList(BaseModel):
    items: list[EventOut]
```

Example:

```json
{
  "items": [
    {
      "id": 18, "title": "ประชุม",
      "start_at": "2026-11-16T08:00:00Z", "end_at": "2026-11-16T09:00:00Z",
      "all_day": false, "todo_id": 43,
      "created_at": "2026-11-15T10:00:00Z", "updated_at": "2026-11-15T10:00:00Z"
    }
  ]
}
```

**Errors:** `401`, `422` (missing/invalid `from`/`to`, or `to < from`).

> **No event write endpoints.** Events are created/updated/deleted *only* through
> their owning todo (FR-5/17; stage-03 OQ-2 default: todo owns the event). The
> schema permits standalone events, but **v1 exposes no HTTP path to create one**
> — keeping todo as the single source of truth. **[DEFAULT ACCEPTED]** If a pure
> calendar-block endpoint is wanted later, it is additive.

---

## 5. Reminders (in-app poll feed + acknowledge)

The background firing job (stage 05, APScheduler) and the Telegram channel are
**not** HTTP — they are internal (stage 02 §3b, §6). The HTTP surface here is
only the **in-app poll feed** and **acknowledge**.

### 5.1 In-app feed contract (resolves OQ-12)

**[DEFAULT ACCEPTED]** — the feed is an **"unacknowledged fired list"**, not a
cursor feed. The frontend polls on its own interval (loosely coupled to the
~30–60 s scan cadence, NFR-19); each poll returns the current set of reminders
that have **fired to the in-app channel and are not yet acknowledged**, so the
frontend renders them until the operator dismisses each. This is simpler and
idempotent (no cursor bookkeeping) at single-user scale.

Backing query (stage 05): `reminder_deliveries` rows with
`channel='in_app' AND status='sent'`, joined to their `reminders` where
`acknowledged_at IS NULL`, ordered by `delivered_at ASC` (served by
`ix_reminder_deliveries_inapp_feed`).

### 5.2 `GET /api/planner/reminders` — poll fired/unacknowledged

- **Private.** FR-30/31a.

**Query params:**

| Param | Type | Default | Notes |
|---|---|---|---|
| `limit` | `int` | `100` | `ge=1, le=500`. |

**Response** `200 OK`, body `ReminderFeed`:

```python
class ReminderFeedItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int  # reminder id (used for ack)
    todo_id: int
    title: str  # denormalized from the owning todo, for display
    fire_at: datetime  # UTC, when it fired
    delivered_at: datetime | None = None  # in-app delivery time (UTC)
    start_at: datetime | None = None  # the item's due time (context), UTC
    is_late: bool = False  # true if delivered via missed-catch-up (FR-34)


class ReminderFeed(BaseModel):
    items: list[ReminderFeedItem]
```

`is_late` marks reminders delivered by the restart catch-up path (OQ-13 note:
late-but-within-window). **[DEFAULT ACCEPTED]** (stage-01 OQ-13: a missed reminder
notes that it is late).

Example:

```json
{
  "items": [
    {
      "id": 90, "todo_id": 43, "title": "ประชุม",
      "fire_at": "2026-11-16T06:00:00Z", "delivered_at": "2026-11-16T06:00:12Z",
      "start_at": "2026-11-16T08:00:00Z", "is_late": false
    }
  ]
}
```

**Errors:** `401`, `422`.

### 5.3 `POST /api/planner/reminders/{reminder_id}/ack` — acknowledge/dismiss

- **Private.** FR-31a. Sets `reminders.acknowledged_at = now()` (status →
  `acknowledged`) so the reminder stops surfacing in the feed. Idempotent-safe.

**Request:** no body. **Response** `200 OK`, body `ReminderOut` (the updated
reminder).

- **Errors:** `401`; `404` (`{"detail": "Reminder not found"}`); `409` only if
  acking a reminder that never fired (still `pending`) —
  `{"detail": "Reminder has not fired yet"}`. Ack-ing an already-acknowledged
  reminder is **idempotent** and returns `200` (not an error), so a double-click
  or re-poll is safe.

> `dismiss` is a synonym for `ack` (US scope excludes snooze/reschedule — stage-01
> Out of Scope). Only this one state-clearing action exists; there is no separate
> dismiss endpoint. **[DEFAULT ACCEPTED]**

---

## 6. Full endpoint table

| # | Method | Path | Auth | Success | Request | Response |
|---|---|---|---|---|---|---|
| 1 | POST | `/api/planner/todos` | private | `201` | `TodoCreate` | `TodoCreated` |
| 2 | POST | `/api/planner/todos/parse` | private | `201` | `TodoParse` | `ParseResult` / `422` `ErrorDetail` / `503` `ErrorDetail` |
| 3 | GET | `/api/planner/todos` | private | `200` | query: `done,date,limit,offset` | `TodoList` |
| 4 | GET | `/api/planner/todos/{todo_id}` | private | `200` | — | `TodoOut` |
| 5 | PATCH | `/api/planner/todos/{todo_id}` | private | `200` | `TodoUpdate` | `TodoCreated` |
| 6 | DELETE | `/api/planner/todos/{todo_id}` | private | `204` | — | — |
| 7 | GET | `/api/planner/events` | private | `200` | query: `from,to` (required) | `EventList` |
| 8 | GET | `/api/planner/reminders` | private | `200` | query: `limit` | `ReminderFeed` |
| 9 | POST | `/api/planner/reminders/{reminder_id}/ack` | private | `200` | — | `ReminderOut` |

**All nine endpoints are private** (gated by `get_current_user`). There are no
public endpoints. There are no event-write or reminder-create HTTP endpoints
(events are owned by todos; reminders are created by the todo create/parse/update
paths and fired by the background job).

---

## 7. Router composition (for stage 05)

Mirror `modules/docker/router.py` — a single `planner_router` with
`prefix="/api"`, `tags=["planner"]`, and `dependencies=[Depends(get_current_user)]`
on each route (or at the router level, since all routes are private):

```python
from fastapi import APIRouter, Depends
from modules.auth.jwt import get_current_user

planner_router = APIRouter(
    prefix="/api",
    tags=["planner"],
    dependencies=[Depends(get_current_user)],  # every route private (FR-21)
)
```

Mounted in `src/main.py` via `app.include_router(planner_router)` alongside the
existing routers.

---

## 8. Requirement / open-question traceability

| FR / AC / OQ | Endpoint(s) / decision |
|---|---|
| FR-1..7, FR-27, AC-1/2/10 | `POST /todos/parse` success (§3.3-A). |
| FR-8/9/9a, AC-3, OQ-1 | parse `422` ambiguous + `503` failed, never persist (§3.3-B/C). **[DEFAULT ACCEPTED]** |
| FR-10, NFR-7/14, AC-3 | parse `503` codes; CRUD unaffected. |
| FR-11, AC-4 | `POST /todos` (no SLM). |
| FR-12, AC-5 | `GET /todos?done=&date=` (§3.2). |
| FR-13/14, FR-17/29 | `PATCH /todos/{id}` (§3.5). |
| FR-15/16 | `DELETE /todos/{id}`, service cascade (§3.6). **[DEFAULT ACCEPTED]** |
| FR-18/20, AC-6, OQ-3 | `GET /events?from=&to=` overlap, null-end=point (§4.1). **[DEFAULT ACCEPTED]** |
| FR-21/22, AC-7 | all routes private, 401 (§1.1, §7). |
| FR-23/24/25/28, AC-11/12 | `reminders`/`ReminderIn`, default-on-omit, `[]`=clear (§3.1/3.5). |
| FR-26, AC-12 | reminders require `start_at` → 422 otherwise. |
| FR-30/31a, OQ-12 | `GET /reminders` unacknowledged-fired feed (§5.1/5.2). **[DEFAULT ACCEPTED]** |
| FR-31a | `POST /reminders/{id}/ack` (§5.3). |
| FR-34, OQ-13 | `ReminderFeedItem.is_late` (§5.2). **[DEFAULT ACCEPTED]** |
| OQ-5/NFR-8 | `TodoParse.text` max 500 chars (§3.3). **[DEFAULT ACCEPTED]** |
| OQ-4 | date-only → `all_day` + `Assumption` (§3.3-A). **[DEFAULT ACCEPTED]** |
| OQ-8, NFR-9 | UTC-on-the-wire everywhere (§0, §1.4). |
| OQ-11/14 | one reminder default UX, model allows many; both channels fixed (no per-reminder channel HTTP selection in v1). **[DEFAULT ACCEPTED]** |

---

## Handoff — to Backend Implementation Agent (stage 05)

**Build these 9 HTTP endpoints** in `src/modules/planner/router.py` (all private,
gated by `get_current_user`; `prefix="/api"`, `tags=["planner"]`):

1. `POST /api/planner/todos` → `201 TodoCreated` (structured, no SLM).
2. `POST /api/planner/todos/parse` → `201 ParseResult` | `422 ErrorDetail`
   (ambiguous) | `503 ErrorDetail` (parser down/timeout/failed). Never persist on
   `422`/`503`.
3. `GET /api/planner/todos` (`done`, `date`, `limit`, `offset`) → `200 TodoList`.
4. `GET /api/planner/todos/{todo_id}` → `200 TodoOut` | `404`.
5. `PATCH /api/planner/todos/{todo_id}` → `200 TodoCreated` | `404` | `422`.
6. `DELETE /api/planner/todos/{todo_id}` → `204` | `404`.
7. `GET /api/planner/events` (`from`, `to` required) → `200 EventList`.
8. `GET /api/planner/reminders` (`limit`) → `200 ReminderFeed`.
9. `POST /api/planner/reminders/{reminder_id}/ack` → `200 ReminderOut` | `404` | `409`.

**Pydantic v2 schemas** to author in `src/modules/planner/schemas.py`: `TodoOut`,
`EventOut`, `ReminderOut`, `Assumption`, `ReminderIn`, `TodoCreate`, `TodoCreated`,
`TodoParse`, `ParseResult`, `ParseCandidate`, `ErrorDetail`, `TodoList`,
`TodoUpdate`, `EventList`, `ReminderFeedItem`, `ReminderFeed`. All response models
use `ConfigDict(from_attributes=True)`; all datetimes serialize UTC; `lead_time`
serializes as ISO-8601 duration (`timedelta`).

**All endpoints are auth-gated** (no public routes). **No event-write endpoints**
(events owned by todos). **No reminder-create endpoint** (reminders come from
todo create/parse/update and the background job).

**Error envelope:** FastAPI default `{"detail": ...}`. Validation → framework
`422`. Parse endpoint returns a typed `ErrorDetail` object as `detail` with a
stable `code` (`parse_ambiguous` [422]; `parser_unavailable` / `parse_timeout` /
`parse_failed` [503]). `404` not-found and `409` conflict use a plain-string
`detail`.

**No MQTT.** The architecture needs no pub/sub beyond Telegram push (one-way,
internal, via `notify` in stage 05); the MQTT section is intentionally omitted.

**Open questions:** all resolved via stage-03-recommended defaults (marked
**[DEFAULT ACCEPTED]** throughout), per the operator's auto-proceed authorization.

**Gate:** The operator authorized stages 04→05→06 to run straight through
**without a review gate**. Stage 05 (Backend Implementation) will therefore
**follow immediately** on this contract without waiting for separate approval.
</content>
</invoke>
