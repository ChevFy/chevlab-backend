# Calendar + To-Do — Requirements (Stage 01)

- **Feature slug:** `calendar-todo`
- **Stage:** 01 — Requirement Analysis
- **Status:** Draft — awaiting approval
- **Author:** Requirement Analysis Agent
- **Date:** 2026-11-15

> Scope note: this document specifies *what* and *why* only. Architecture,
> data schema, API payload shapes, and LLM integration mechanics are decided in
> later stages (02+). Statements below about tables and endpoints describe
> intended behaviour and surface, not a committed design.

---

## 1. Overview

`calendar-todo` adds a personal, single-user calendar and to-do system to the
chevlab-backend — a self-hosted, lightweight alternative to the task/calendar
surface of tools like Notion. The distinguishing capability is **natural-language
capture**: the operator types a free-form line (Thai or English, e.g.
`ประชุมทีมพรุ่งนี้บ่าย 3 โมง` or `pay rent on the 1st`), the backend parses it
into structured fields, and records it as a to-do and — when a time is present —
as a calendar event. Parsing runs **locally** on a small, CPU-only, GPU-less
mini-PC (no cloud dependency for the default path): a small language model
(SLM via Ollama) extracts the title and a rough date/time expression, and a
deterministic date-parsing library resolves that expression to a concrete
Asia/Bangkok datetime. To-dos and events are **separate but linked** concepts: a
to-do is a task (optionally with a due time); an event is a time block rendered
on a calendar grid. Dated items can carry **reminders** — lead-time
notifications before the due time — delivered both to Telegram (via the existing
`notify` module) and in-app (polled by the frontend); a background job fires each
reminder exactly once. The feature serves a web frontend that provides a
chat/input box for capture and a calendar/list view for display. Like the rest
of this project, it is strictly single-user and gated behind the existing JWT
auth.

---

## 2. Roles

There is exactly one role.

- **Operator** — the single authenticated homelab owner. Same identity model as
  the rest of chevlab-backend (`ADMIN_USERNAME`, JWT bearer token). No multi-user,
  no sharing, no roles/permissions.

---

## 3. User Stories

- **US-1 (NL capture):** As the operator, I want to type a to-do in plain Thai or
  English and have the system figure out the title and time, so that I can capture
  tasks quickly without filling in a form.
- **US-2 (structured create):** As the operator, I want to create a to-do by
  providing fields directly (no parsing), so that I have a reliable path when I
  already know the exact title and time or when parsing is unavailable.
- **US-3 (floating task):** As the operator, I want to create a to-do with no due
  date, so that I can track tasks that are not tied to a specific time.
- **US-4 (dated task → calendar):** As the operator, I want a to-do that has a due
  time to also appear on my calendar, so that I see it in context with my other
  time blocks.
- **US-5 (list & filter):** As the operator, I want to list my to-dos and filter
  by completion status and by date, so that I can see what is pending or due.
- **US-6 (calendar view):** As the operator, I want to fetch all events in a date
  range, so that the frontend can render a day/week/month calendar grid.
- **US-7 (complete/edit):** As the operator, I want to mark a to-do done, edit its
  title or time, so that I can keep my list accurate.
- **US-8 (delete):** As the operator, I want to delete a to-do, so that I can
  remove things I no longer need.
- **US-9 (parse feedback):** As the operator, when the system cannot confidently
  parse my text, I want clear feedback about what went wrong or what it assumed,
  so that I can correct it instead of silently getting a wrong entry.
- **US-10 (privacy):** As the operator, I want all of my calendar and to-do data
  to require login, so that it is not exposed on the public internet.
- **US-11 (be reminded):** As the operator, I want to be reminded before a dated
  to-do/event is due, so that I do not miss it.
- **US-12 (reminder lead time):** As the operator, I want to set how far ahead I
  am reminded (e.g. 1 day, 1 hour, on the day), and to have a sensible default
  applied when I do not say, so that I do not have to specify it every time.
- **US-13 (reminder by voice/NL):** As the operator, I want to state the reminder
  in the same natural-language line ("remind me 2 hours before" / "เตือนก่อน 1
  วัน"), so that capture stays a single step.
- **US-14 (reminder channels):** As the operator, I want reminders delivered both
  to Telegram and inside the app, so that I notice them wherever I am.
- **US-15 (reliable firing):** As the operator, I want each reminder to fire once
  and not be lost or repeated across a restart, so that I can trust the system.

---

## 4. Functional Requirements

Capture / parsing:

- **FR-1** The system shall accept raw natural-language text and return a
  structured result containing at least: `title`, `start_at`, optional `end_at`,
  and `all_day` (boolean).
- **FR-2** Parsing shall support both Thai and English input in the same system
  (and reasonably handle mixed-language lines).
- **FR-3** Parsing shall resolve relative dates and times (e.g. `พรุ่งนี้`,
  `อังคารหน้า`, `tomorrow`, `next Monday`, `บ่าย 3 โมง`, `in 2 hours`) relative to
  the current date/time, which the system supplies ("today") so relative
  expressions resolve correctly.
- **FR-3a** Parsing shall use a **hybrid** approach: the SLM extracts the `title`
  and a *rough date/time expression* (natural-language, not a resolved
  timestamp), and a deterministic date-parsing library (e.g. `dateparser`)
  resolves that expression to a concrete datetime. The SLM shall not be relied
  upon to compute final datetimes, because small models make arithmetic/calendar
  errors.
- **FR-3b** The parser shall be implemented behind a **swappable interface**
  (a `Parser` abstraction). The default implementation is the local SLM via
  Ollama; an optional cloud implementation (e.g. Claude API) shall be pluggable
  as a fallback for when more capable hardware is available. No part of the
  feature outside the parser implementation shall couple to a specific provider.
- **FR-4** All parsed and stored times shall be interpreted and resolved in the
  `Asia/Bangkok` timezone, regardless of server locale. The date-parsing library
  shall be configured so relative expressions anchor to the supplied "today" in
  Asia/Bangkok and prefer future dates for ambiguous day-of-week expressions.
- **FR-5** On a successful parse, the system shall create a to-do; if the parse
  yields a time (`start_at`), it shall also create a linked calendar event and
  associate it with the to-do.
- **FR-6** If the parse yields no time, the to-do shall be created as a floating
  task with no linked event.
- **FR-7** The system shall return the created to-do (and the linked event, if
  any) in the parse response, so the frontend can display the result immediately.
- **FR-8** When the parse is ambiguous or low-confidence (e.g. a date with no
  clear time, or multiple plausible interpretations), the system shall surface
  this to the caller rather than silently guessing — either by returning the
  assumption made (see FR-9) or by returning a recoverable error (see FR-10).
  *(Exact contract — assumption vs. error vs. confirmation step — is an Open
  Question, see §7.)*
- **FR-9** When the system fills a default (e.g. assumes `all_day` because no time
  was given, or assumes the nearest future occurrence of a weekday), the response
  shall make the assumption visible to the operator.
- **FR-9a** When the SLM extracts a date/time expression that the date-parsing
  library cannot resolve, the system shall treat it as an undated/ambiguous case
  (not fabricate a time) and surface it per FR-8/FR-10.
- **FR-10** When parsing fails entirely (the SLM is unavailable, returns no usable
  structure, errors, or times out), the system shall return a clear, recoverable
  error that does NOT create a malformed to-do, and shall allow the operator to
  retry or fall back to structured create (FR-11).

To-dos (CRUD):

- **FR-11** The system shall allow creating a to-do from structured fields
  directly, without invoking the SLM parser.
- **FR-12** The system shall allow listing to-dos, with filters for completion
  status (done / not done) and for date.
- **FR-13** The system shall allow updating a to-do (at minimum: title,
  start/end time, all-day flag, done status).
- **FR-14** The system shall allow marking a to-do complete and incomplete.
- **FR-15** The system shall allow deleting a to-do.
- **FR-16** When a to-do that has a linked event is deleted or has its time
  removed, the system shall keep the to-do and its linked event consistent (the
  precise rule — cascade delete the event, orphan it, or forbid — is an Open
  Question, see §7).
- **FR-17** When a floating to-do (no event) is updated to include a due time, the
  system shall create and link a corresponding event; when a dated to-do has its
  time removed, the linked event shall be handled per FR-16.

Events / calendar:

- **FR-18** The system shall allow listing events within a date range specified by
  a start and end bound (`from`, `to`), ordered for calendar rendering.
- **FR-19** Each event shall carry at least a title, `start_at`, optional
  `end_at`, `all_day` flag, and a reference to its source to-do (if any).
- **FR-20** The range query shall return events whose time span overlaps the
  requested window (not only those that start inside it).

Auth / access:

- **FR-21** Every calendar and to-do endpoint shall require a valid authenticated
  session; unauthenticated requests shall be rejected with `401`.
- **FR-22** All data shall belong to the single operator identity; no endpoint
  shall accept or require a user/owner parameter.

Reminders — model & capture:

- **FR-23** A dated to-do/event shall be able to carry one or more **reminders**,
  each expressed as a **lead time before the due datetime** (e.g. 1 day before,
  1 hour before, or on-the-day at a given time). *(Whether multiple reminders per
  item ship in v1 or just a single reminder is an Open Question, see §7; the model
  shall not preclude multiple.)*
- **FR-24** When a dated item is created without any explicit reminder, the system
  shall apply a **default reminder of 1 day before** the due datetime.
- **FR-25** The default reminder lead time shall be **configurable via settings**
  (pydantic-settings / `.env`), with `1 day` as the shipped default value; it
  shall not be hard-coded.
- **FR-26** A to-do with **no due date (floating task)** shall have **no
  reminder**; reminders require a due datetime.
- **FR-27** The natural-language parse (SLM + `dateparser` hybrid) shall recognize
  reminder phrases in the input text (e.g. `เตือนก่อน 1 วัน`,
  `remind me 2 hours before`, `เตือนตอนเช้าวันนั้น`) and set the reminder lead
  time accordingly; when no reminder phrase is present but a due datetime is,
  it shall fall back to the configured default (FR-24/FR-25).
- **FR-28** Structured create/update (non-parse) shall accept explicit reminder
  lead times directly, and shall allow clearing reminders.
- **FR-29** When an item's due datetime changes, its pending (not-yet-fired)
  reminders shall be recomputed against the new due time; already-fired reminders
  shall not re-fire (see FR-32).

Reminders — delivery:

- **FR-30** Reminders shall be deliverable to **both** channels: **Telegram** via
  the existing `notify` module, and **in-app** via an endpoint the frontend polls
  for due/fired reminders. Both are required for a reminder by default.
- **FR-31** Delivery shall go through an abstraction that lets a single reminder
  **fan out to multiple channels**; adding or removing a channel shall not change
  reminder scheduling logic. *(Whether per-reminder channel selection is operator-
  configurable is an Open Question, see §7.)*
- **FR-31a** The in-app channel shall expose an endpoint (auth-gated) the frontend
  can poll to retrieve reminders that have fired / are due to be surfaced, and a
  way to acknowledge/dismiss them so they are not shown repeatedly. *(Exact
  contract — fired-since cursor vs. unacknowledged list — is an Open Question,
  see §7.)*

Reminders — background delivery job:

- **FR-32** A scheduled/background task shall scan for reminders whose fire-time
  has arrived and dispatch them. Each reminder shall fire **exactly once**: the
  job shall be debounced/idempotent by tracking a per-reminder **sent state**, so
  a reminder is not dispatched repeatedly.
- **FR-33** Reminder scheduling shall be **durable across restarts**: pending
  reminders and their sent state are persisted so a reboot neither loses nor
  double-fires reminders. The job shall reuse or align with the existing
  background-sampler/scheduler pattern (FastAPI lifespan + APScheduler) described
  in `CLAUDE.md`, and run off the event loop so it does not block request handling
  or the metrics sampler.
- **FR-34** If a reminder's fire-time passed while the service was down, the job
  shall, on restart, deliver still-relevant missed reminders rather than skip them
  silently. *(The catch-up window / "too late to bother" cutoff is an Open
  Question, see §7.)*

---

## 5. Non-Functional Requirements

Performance, hardware & resource contention:

> **Deployment target:** Lenovo ThinkCentre M710q — Intel i5 (7th gen), 8 GB RAM,
> 256 GB SSD, **no GPU, CPU-only**. This same box also runs the monitoring
> backend, PostgreSQL, and Docker. RAM (8 GB total) is the binding constraint.

- **NFR-1** A structured create / list / update / delete (non-SLM) request shall
  respond within a typical interactive budget (target p95 < 300 ms under
  single-user load, excluding network).
- **NFR-2** An NL-parse request runs a local SLM on CPU. A per-parse latency of
  roughly **2–6 s is acceptable**, because parsing is occasional and on-demand.
  The request shall have a bounded timeout; if the model does not respond within
  it, the request shall fail per FR-10 rather than hang.
- **NFR-3** SLM usage shall be minimized per capture: one model call per parse
  request producing structured output (extracting `title` + rough date/time
  expression). Retry logic must not silently multiply calls or compound latency.
- **NFR-4** The default parser shall be a **local SLM served via Ollama**, model
  **`qwen2.5:3b-instruct`** (q4 quantization, ~2.3 GB), CPU-only, supporting Thai
  and English. No cloud API call is required for the default path.
- **NFR-5** **RAM contention is a key risk** on the 8 GB box shared with Postgres,
  Docker, and the metrics backend. Ollama shall be configured with a short
  `keep_alive` (e.g. ~5 minutes) so the model is unloaded and its RAM released
  when idle, accepting a cold-start reload cost on the next parse. The model must
  not be pinned resident.
- **NFR-6** Parsing shall **not block** the FastAPI event loop or the metrics
  sampler/scheduler. The SLM call (and any blocking date-parsing work) shall run
  off the event loop (async client or worker/thread offload) so an in-flight parse
  does not delay metric sampling or other requests.
- **NFR-7** Parser configuration — Ollama host/URL, model name, `keep_alive`,
  timeout, and which parser implementation is active (local vs. optional cloud
  fallback, plus any cloud API key) — shall be provided via pydantic-settings /
  `.env` (never `os.environ` directly, never hard-coded). An unavailable or
  unconfigured parser shall disable the parse endpoint gracefully, not crash the
  app (see NFR-16/NFR-17).
- **NFR-8** Parse requests shall be safeguarded against runaway cost/latency: an
  upper bound on input text length shall be enforced. *(Exact limit is an Open
  Question.)*

Correctness:

- **NFR-9** Timezone handling shall be correct and unambiguous: inputs are
  resolved in `Asia/Bangkok`; stored/returned timestamps follow the project-wide
  convention (the rest of the API stores and returns UTC — see §8). The storage
  vs. wire representation must be decided in stage 02 and applied consistently.
- **NFR-10** Day-boundary and DST edge cases must behave correctly for
  `Asia/Bangkok` (note: Bangkok has no DST, which simplifies this, but the range
  queries in FR-18/FR-20 must still handle "end of day" boundaries correctly).

Security & privacy:

- **NFR-11** Reuse the existing auth mechanism (`get_current_user` JWT bearer
  dependency) rather than introducing a new auth path.
- **NFR-12** With the default local SLM parser, operator text stays on-device and
  is **not** sent to any third party. If the optional cloud parser (e.g. Claude
  API) is enabled, that data flow sends the operator's typed text + current
  date/time off-box and shall be documented and explicitly opt-in; no
  metrics/system data is ever included in the prompt.
- **NFR-13** The parse endpoint shall not be exploitable to make the backend issue
  arbitrary outbound requests beyond the configured parser provider (the local
  Ollama host, or the explicitly configured cloud provider).

Reliability & observability:

- **NFR-14** A parser outage (Ollama down, model not pulled, or cloud provider
  unreachable) shall degrade only the `parse` capability; structured CRUD,
  listing, and calendar rendering shall remain fully functional.
- **NFR-15** Parse failures, timeouts, model cold-starts, and ambiguous-assumption
  events shall be logged with enough context to diagnose, without logging secrets.
- **NFR-16** The feature shall follow the project module layout
  (`src/modules/<feature>/` owning its router, schemas, models, service) and
  mount under the existing `/api` router prefix convention.

Operational prerequisites:

- **NFR-17** The default parser requires an Ollama runtime reachable from the
  backend with the `qwen2.5:3b-instruct` model pulled. Deployment/compose and
  docs shall account for this (and for the ~2.3 GB model footprint on the 8 GB
  host). The app shall start and run with the parse endpoint disabled if Ollama
  is absent, rather than failing to boot.

Reminders — reliability & performance:

- **NFR-18** The reminder-delivery job shall be **idempotent and crash-safe**: the
  sent-state transition shall be persisted such that a crash mid-dispatch does not
  cause a duplicate send on recovery, and shall tolerate at-least-once dispatch
  attempts without the operator seeing duplicates (align with the firing contract
  in FR-32/FR-33).
- **NFR-19** The reminder scan shall run on a background schedule at a cadence that
  bounds firing lateness to an acceptable granularity (e.g. scan every ~30–60 s so
  a reminder fires within that window of its lead time). It shall be lightweight
  and shall **not block the FastAPI event loop or the metrics sampler**, honoring
  the 8 GB CPU-only host constraints.
- **NFR-20** A Telegram-delivery failure (`notify`/Telegram unreachable) shall not
  lose the reminder or block in-app delivery: channels shall fail independently,
  the failure shall be logged, and the system should allow a bounded retry rather
  than silently dropping. *(Exact retry policy is an Open Question.)*
- **NFR-21** Reminder firing, per-channel delivery outcomes, missed-reminder
  catch-up, and acknowledgements shall be logged with enough context to diagnose,
  without logging secrets (e.g. no bot tokens).
- **NFR-22** The reminder subsystem depends on the existing `notify` module for
  Telegram; it shall **reuse** that module rather than open a second Telegram
  path. If `notify` is unconfigured/unavailable, reminders shall still fire in-app
  and the app shall not crash (graceful degradation).

Data retention:

- **NFR-23** Unlike metric samples, to-dos and events are user-authored records
  and shall be retained indefinitely (no automatic rollup/pruning). *(Whether
  completed/old items are ever auto-archived is an Open Question.)* Fired-reminder
  records may be pruned/archived once acknowledged and past, per the retention
  decision in §7.

---

## 6. Acceptance Criteria

**AC-1 (US-1, FR-1..FR-5) — NL capture with a time**
- *Given* the operator is authenticated and today is a known date,
- *When* they submit the text `ประชุมทีมพรุ่งนี้บ่าย 3 โมง`,
- *Then* a to-do is created with title ≈ "ประชุมทีม", `start_at` = tomorrow 15:00
  Asia/Bangkok, `all_day` = false, and a linked event is created and returned.

**AC-2 (US-3, FR-6) — floating task**
- *Given* the operator is authenticated,
- *When* they submit text with no time (e.g. `buy milk`),
- *Then* a to-do is created with no linked event, and the response indicates it is
  a floating (undated) task.

**AC-3 (US-9, FR-8..FR-10) — parse failure is recoverable**
- *Given* the operator is authenticated,
- *When* the SLM call fails, the model is unavailable, or it returns no usable
  structure,
- *Then* the request returns a clear error, no malformed to-do is persisted, and
  the operator can retry or use structured create.

**AC-4 (US-2, FR-11) — structured create bypasses the parser**
- *Given* the operator is authenticated,
- *When* they create a to-do with explicit fields,
- *Then* the to-do is created without any SLM call, with a linked event iff a time
  was provided.

**AC-5 (US-5, FR-12) — filtered list**
- *Given* several to-dos exist, some done and some due on specific dates,
- *When* the operator lists to-dos filtered by done status and/or date,
- *Then* only matching to-dos are returned.

**AC-6 (US-6, FR-18, FR-20) — calendar range**
- *Given* events exist, including one spanning the start of the window,
- *When* the operator requests events for a `from`/`to` range,
- *Then* all events overlapping the window are returned, ordered for rendering.

**AC-7 (US-10, FR-21) — auth gating**
- *Given* a request with no or invalid token,
- *When* it hits any calendar/to-do endpoint,
- *Then* it is rejected with `401` and no data is returned.

**AC-8 (FR-4, NFR-9) — timezone correctness**
- *Given* the server runs in UTC,
- *When* the operator says `tomorrow 9am`,
- *Then* the resolved time corresponds to 09:00 Asia/Bangkok on the next day (not
  09:00 UTC).

**AC-9 (US-11/US-12, FR-24/FR-25, FR-30, FR-32) — default reminder fires once to both channels**
- *Given* the operator creates a to-do due tomorrow 15:00 with no reminder stated,
  and the default lead time is the shipped `1 day`,
- *When* the reminder's fire-time (today 15:00 Asia/Bangkok) arrives,
- *Then* exactly one reminder is dispatched to both Telegram (via `notify`) and the
  in-app channel, and it is not dispatched again on subsequent scans.

**AC-10 (US-13, FR-27) — reminder lead time from natural language**
- *Given* the operator submits `ประชุมพรุ่งนี้บ่าย 3 โมง เตือนก่อน 2 ชั่วโมง`,
- *When* the text is parsed,
- *Then* the to-do is due tomorrow 15:00 Asia/Bangkok and a reminder is set for
  2 hours before (13:00), overriding the default.

**AC-11 (US-12, FR-25) — configurable default**
- *Given* the default-reminder setting is changed via `.env` (e.g. to 2 hours),
- *When* a dated to-do is created with no reminder stated,
- *Then* the reminder is scheduled 2 hours before due (the new configured default),
  with no code change.

**AC-12 (FR-26) — floating task has no reminder**
- *Given* the operator creates a to-do with no due date,
- *When* it is created,
- *Then* no reminder is scheduled for it.

**AC-13 (US-15, FR-33) — survives restart without loss or duplicate**
- *Given* a reminder is scheduled for a future fire-time and persisted,
- *When* the service restarts before the fire-time and then the fire-time arrives,
- *Then* the reminder still fires exactly once (not lost, not duplicated).

**AC-14 (FR-34) — missed reminder catch-up after downtime**
- *Given* a reminder's fire-time passed while the service was down, and it is
  within the catch-up window,
- *When* the service restarts,
- *Then* the still-relevant reminder is delivered rather than silently skipped.

---

## 7. Open Questions

1. **Ambiguity contract (FR-8):** When the parse is low-confidence, should the API
   (a) create the to-do with its best guess and flag the assumption, (b) return
   a 422-style "please confirm/clarify" without creating, or (c) return the
   parsed candidate for a two-step confirm-then-create flow? This shapes the
   parse endpoint contract and the frontend UX.
2. **Event/todo link lifecycle (FR-16):** On deleting a dated to-do or removing
   its time — cascade-delete the linked event, orphan it as a standalone event,
   or forbid? And can an event exist without a parent to-do (e.g. a pure calendar
   block)?
3. **Default duration:** When only `start_at` is parsed (no `end_at`) and the item
   is not all-day, is it a zero-length point, or does it get a default duration
   (e.g. 1h) for calendar rendering?
4. **All-day inference:** When a date but no time is given (e.g. "pay rent on the
   1st"), is that an `all_day` event, a floating to-do with a due date, or does it
   prompt for a time?
5. **Parse timeout, input length & cold-start (NFR-2, NFR-5, NFR-8):** Concrete
   values for the parse timeout, the maximum input text length, and the Ollama
   `keep_alive` window (trade-off: shorter frees RAM sooner but adds cold-start
   latency to the next parse).
6. **Recurrence:** Are recurring tasks/events ("every Monday", "ทุกวันจันทร์") in
   scope now, or explicitly later? (Currently assumed out of scope — see §9.)
7. **Retention/archival (NFR-23):** Should completed or long-past items ever be
   auto-archived or purged, or kept forever?
8. **Storage vs. wire timezone (NFR-9):** Confirm the convention — store UTC and
   return UTC (matching existing endpoints) with the client converting to
   Asia/Bangkok for display, or return Asia/Bangkok-local times for this feature?
9. **Cloud fallback trigger (FR-3b, NFR-4):** Is the cloud parser selected purely
   by configuration (operator switches implementation), or should the system ever
   auto-fall-back to the cloud at runtime when the local SLM is unavailable or
   low-confidence? Auto-fallback has privacy implications (NFR-12) and should be
   explicit, not silent.
10. **SLM accuracy acceptance bar:** What Thai/English parse accuracy from
    `qwen2.5:3b-instruct` is "good enough" to ship, and is there a representative
    test set of phrases to validate against? Small models may need prompt tuning
    or a larger model if accuracy is insufficient on the 8 GB box.
11. **Multiple reminders per item (FR-23):** Does v1 support more than one reminder
    per item (e.g. 1 day AND 1 hour before), or exactly one? The data model should
    allow many; the question is the shipped UX/API scope.
12. **In-app poll contract (FR-31a):** What exactly does the in-app endpoint
    return — a "fired since `<cursor>`" feed, or a list of unacknowledged
    reminders? How are reminders acknowledged/dismissed, and how does the frontend
    poll interval relate to the scan cadence (NFR-19)?
13. **Missed-reminder catch-up window (FR-34):** After downtime, how late is "too
    late" to still deliver a missed reminder (e.g. deliver if < N hours late, else
    drop)? And should a missed reminder note that it is late?
14. **Per-reminder channel selection (FR-30/FR-31):** Is "both Telegram and in-app"
    fixed, or can the operator choose channels per reminder / globally?
15. **Telegram retry policy (NFR-20):** On Telegram delivery failure, how many
    retries over what window before giving up, and is the in-app channel considered
    the reliable fallback?

---

## 8. Assumptions

- **A-1** The auth module already exists and exposes a reusable gating dependency
  (`get_current_user`, JWT bearer, 1-hour tokens). *This corrects the task-context
  note that auth "does not yet exist" — it is present in `src/modules/auth/`.* This
  feature reuses it and does not build new auth.
- **A-2** Endpoints will live under the existing `/api` router prefix (every
  current router uses `prefix="/api"`). The logical paths in the task context
  (`/calendar/todos`, `/calendar/events`, …) are therefore expected to be mounted
  as e.g. `/api/calendar/todos`. Exact path strings are finalized in stage 02.
- **A-3** The project-wide timestamp convention is UTC on the wire (README:
  "Timestamps are in UTC"). This feature's Asia/Bangkok handling is about
  *resolving* operator input, not necessarily about the stored/returned format —
  see Open Question 8.
- **A-4** The default parser is a **local SLM via Ollama** (`qwen2.5:3b-instruct`,
  q4, CPU-only), running on the deployment target (Lenovo ThinkCentre M710q, i5
  7th gen, 8 GB RAM, no GPU) alongside the monitoring backend, Postgres, and
  Docker. The SLM extracts title + a rough date/time expression; a date-parsing
  library (`dateparser`) resolves it to a concrete Asia/Bangkok datetime. A cloud
  parser (e.g. Claude API) is an optional, pluggable fallback behind the same
  interface, not the default. *This replaces the earlier assumption that the
  default parser was the Anthropic Claude `claude-haiku-4-5` API.*
- **A-4a** An Ollama runtime is (or will be) available to the backend with the
  model pulled; the ~2.3 GB model and tuned short `keep_alive` fit within the
  8 GB RAM budget shared with the other services.
- **A-5** A single `api` process runs (per README limitation); there is no
  multi-worker concern for these endpoints beyond what already applies.
- **A-6** The frontend provides the "chat"/input box and the calendar grid; this
  spec covers the backend contract only.

---

## 9. Out of Scope

- Multi-user, sharing, collaboration, permissions, or per-user data isolation.
- Recurring/repeating tasks and events (pending Open Question 6) — including
  recurring reminders; reminders in v1 attach to a single-occurrence due time.
- Snooze/reschedule of an already-fired reminder (beyond acknowledge/dismiss).
- Push/email/SMS or any reminder channel other than Telegram and in-app.
- Sub-tasks, checklists within a to-do, tags/labels, priorities, projects, or
  boards.
- Rich text, attachments, or file storage on to-dos/events.
- External calendar sync (Google Calendar, CalDAV, iCal import/export).
- Any frontend UI, rendering, or client-side logic.
- Architecture, database schema, migration, and concrete API payload design
  (stages 02+).
- Streaming or conversational multi-turn LLM interaction (parse is single-shot).

---

## Handoff

- **Feature slug:** `calendar-todo`
- **Headline functional scope:** A single-user calendar + to-do backend with
  natural-language capture, running fully on-box on a CPU-only mini-PC. The
  operator types free-form Thai/English text; a **local SLM (Ollama,
  `qwen2.5:3b-instruct`, CPU-only)** extracts `title` + a rough date/time
  expression, and a date-parsing library (`dateparser`) resolves that expression
  to a concrete Asia/Bangkok datetime (with "today" supplied) — a **hybrid** split
  chosen so the small model never does the date arithmetic. The result yields
  `{title, start_at, end_at?, all_day}`, then creates a **to-do** and, if dated, a
  **linked event**. To-dos and events are separate-but-linked (a dated to-do owns
  an event; an undated to-do floats). The parser sits behind a swappable
  interface; a cloud model (Claude API) is an optional fallback, not the default.
  Dated items carry **reminders** (lead time before due; default `1 day`,
  configurable via settings; also extracted from NL like "remind me 2h before").
  A durable, idempotent background job fires each reminder **exactly once** and
  fans it out to **both Telegram (via the existing `notify` module) and in-app**
  (a polled endpoint). Endpoints cover: NL parse-and-create, structured create,
  list/filter to-dos, patch/delete to-do, list events in a date range for a
  calendar grid, and poll/acknowledge in-app reminders. All endpoints are
  auth-gated and single-user.

- **Top constraints the Stage 02 (System Architecture) Agent must honor:**
  1. **Reuse existing auth** — gate every endpoint with the existing
     `get_current_user` JWT dependency in `src/modules/auth/`; do not invent new
     auth. (Corrects the context note claiming auth is absent.)
  2. **Follow project conventions** — module-per-feature under
     `src/modules/calendar-todo/` (router/schemas/models/service), mounted under
     the existing `/api` prefix; config via pydantic-settings/`.env` only, never
     `os.environ`; SQLAlchemy 2 + Alembic, new models registered in
     `src/migrations/env.py`.
  3. **Hardware budget is the binding constraint** — the target is an 8 GB,
     CPU-only ThinkCentre M710q shared with Postgres + Docker + the metrics
     backend. Keep the SLM footprint (~2.3 GB) unloaded when idle (short Ollama
     `keep_alive`), run the parse off the event loop so it never blocks the metrics
     sampler, and treat ~2–6 s CPU parse latency as acceptable.
  4. **Swappable parser behind an interface** — default = local Ollama SLM;
     optional cloud (Claude API) fallback must plug into the same abstraction with
     no provider coupling leaking into the rest of the feature.
  5. **Hybrid date resolution** — SLM extracts title + rough date expression only;
     `dateparser` resolves concrete datetimes in Asia/Bangkok; reconcile with the
     project's UTC-on-the-wire convention (Open Question 8).
  6. **Parser isolation & resilience** — one model call per parse, bounded timeout;
     a parser outage (Ollama down / model not pulled / cloud unreachable) or
     missing config must degrade *only* the parse endpoint and must not prevent the
     app from booting, leaving CRUD and calendar rendering fully functional.
  7. **Parse-failure/ambiguity handling is a first-class concern** — never persist
     a malformed to-do on parse failure; make assumptions visible; contract shape
     pending Open Question 1.
  8. **Reminders — exactly-once, durable, non-blocking** — persist reminders and
     their sent-state; the background firing job must be idempotent, survive
     restarts without loss or double-fire, catch up on missed reminders within a
     window, and run off the event loop without blocking the metrics sampler.
     Reuse/align with the existing lifespan + APScheduler sampler pattern. The
     default lead time (`1 day`) is settings-configurable, not hard-coded.
  9. **Reminder delivery fan-out** — a reminder dispatches to multiple channels via
     a delivery abstraction; **reuse the existing `notify` module** for Telegram
     (do not open a second Telegram path) and expose a polled in-app channel.
     Channels fail independently; in-app still works if Telegram/`notify` is down.
  10. **Retention** — to-dos/events are user-authored and retained indefinitely; do
      not apply the metrics-style rollup/pruning.

- **Unresolved Open Questions (must be answered before/with stage 02):** ambiguity
  contract (1); event↔todo link lifecycle and standalone events (2); default
  duration for timed items (3); all-day inference for date-only input (4); parse
  timeout, max input length and Ollama `keep_alive` window (5); recurrence
  in/out of scope (6); retention/archival policy (7); storage vs. wire timezone
  representation (8); cloud-fallback trigger / auto-fallback privacy (9); SLM
  parse-accuracy acceptance bar and test set (10); multiple reminders per item
  (11); in-app reminder poll/acknowledge contract (12); missed-reminder catch-up
  window (13); per-reminder channel selection (14); Telegram retry policy (15).

- **Gate:** Do **not** start stage 02 (System Architecture) until this
  requirements document is reviewed and approved by the human operator.
