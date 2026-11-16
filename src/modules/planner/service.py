"""Planner orchestration: todo/event CRUD, the todo↔event link lifecycle,
reminder (re)computation, and the NL parse-and-create flow.

All mutations use ``async with SessionLocal()`` (matching ``system/service.py``)
and keep todo + event + reminder changes in a single transaction so they stay
consistent (FR-5/16/17). Datetimes are UTC at rest (store-UTC, NFR-9).
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import SessionLocal

from . import schemas
from .models import Event, Reminder, ReminderDelivery, Todo
from .parser.base import Parser, ParserError, ParserUnavailable
from .parser.resolver import resolve_date, resolve_lead_time
from .reminders import service as reminder_service

logger = logging.getLogger(__name__)


class TodoNotFound(Exception):
    pass


class ReminderNotFound(Exception):
    pass


class ReminderNotFired(Exception):
    pass


class ParseAmbiguous(Exception):
    """Title found but date/reminder could not be confidently resolved (→ 422)."""

    def __init__(self, candidate: schemas.ParseCandidate):
        self.candidate = candidate


# --------------------------------------------------------------------------
# Serialization helpers (Event has no back-FK column, so todo_id is injected)
# --------------------------------------------------------------------------


def _event_out(event: Event | None, *, todo_id: int | None) -> schemas.EventOut | None:
    if event is None:
        return None
    return schemas.EventOut(
        id=event.id,
        title=event.title,
        start_at=event.start_at,
        end_at=event.end_at,
        all_day=event.all_day,
        todo_id=todo_id,
        created_at=event.created_at,
        updated_at=event.updated_at,
    )


def _todo_created(
    todo: Todo, event: Event | None, reminders: list[Reminder]
) -> schemas.TodoCreated:
    return schemas.TodoCreated(
        todo=schemas.TodoOut.model_validate(todo),
        event=_event_out(event, todo_id=todo.id),
        reminders=[schemas.ReminderOut.model_validate(r) for r in reminders],
    )


# --------------------------------------------------------------------------
# Internal mutation primitives (operate on a provided session)
# --------------------------------------------------------------------------


async def _create_event_for_todo(session: AsyncSession, todo: Todo) -> Event:
    event = Event(
        title=todo.title,
        start_at=todo.start_at,
        end_at=todo.end_at,
        all_day=todo.all_day,
    )
    session.add(event)
    await session.flush()
    todo.event_id = event.id
    return event


async def _apply_reminders(
    session: AsyncSession,
    todo: Todo,
    event: Event | None,
    reminders_in: list[schemas.ReminderIn] | None,
) -> list[Reminder]:
    """Schedule reminders for a dated todo (FR-24/28)."""
    if todo.start_at is None:
        return []
    lead_deltas = None if reminders_in is None else [r.lead_time for r in reminders_in]
    lead_specs = reminder_service._lead_specs_from_ins(lead_deltas)
    if not lead_specs:
        return []
    created = await reminder_service.schedule_reminders(
        session,
        todo_id=todo.id,
        event_id=event.id if event else None,
        due=todo.start_at,
        lead_specs=lead_specs,
    )
    return created


# --------------------------------------------------------------------------
# Structured create (FR-11, no SLM)
# --------------------------------------------------------------------------


async def create_todo(
    body: schemas.TodoCreate, *, source_text: str | None = None
) -> schemas.TodoCreated:
    async with SessionLocal() as session:
        todo = Todo(
            title=body.title,
            source_text=source_text,
            start_at=body.start_at,
            end_at=body.end_at,
            all_day=body.all_day,
        )
        session.add(todo)
        await session.flush()

        event: Event | None = None
        if todo.start_at is not None:
            event = await _create_event_for_todo(session, todo)
            await session.flush()

        reminders = await _apply_reminders(session, todo, event, body.reminders)
        await session.commit()

        await session.refresh(todo)
        if event is not None:
            await session.refresh(event)
        for r in reminders:
            await session.refresh(r)
        return _todo_created(todo, event, reminders)


# --------------------------------------------------------------------------
# List / get (FR-12)
# --------------------------------------------------------------------------


def _local_day_window_utc(day: date) -> tuple[datetime, datetime]:
    """Translate an Asia/Bangkok calendar day to a UTC half-open window (NFR-10)."""
    tz = ZoneInfo(settings.PLANNER_RESOLVE_TZ)
    start_local = datetime.combine(day, time(0, 0), tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


async def list_todos(
    *,
    done: bool | None,
    day: date | None,
    limit: int,
    offset: int,
) -> schemas.TodoList:
    conditions = []
    if done is not None:
        conditions.append(Todo.done == done)
    if day is not None:
        start_utc, end_utc = _local_day_window_utc(day)
        conditions.append(Todo.start_at >= start_utc)
        conditions.append(Todo.start_at < end_utc)

    async with SessionLocal() as session:
        total = await session.scalar(
            select(func.count()).select_from(Todo).where(*conditions)
        )
        result = await session.scalars(
            select(Todo)
            .where(*conditions)
            .order_by(Todo.start_at.asc().nulls_last(), Todo.id.asc())
            .limit(limit)
            .offset(offset)
        )
        items = [schemas.TodoOut.model_validate(t) for t in result.all()]
    return schemas.TodoList(items=items, total=total or 0, limit=limit, offset=offset)


async def get_todo(todo_id: int) -> schemas.TodoOut:
    async with SessionLocal() as session:
        todo = await session.get(Todo, todo_id)
        if todo is None:
            raise TodoNotFound
        return schemas.TodoOut.model_validate(todo)


# --------------------------------------------------------------------------
# Update with link lifecycle (FR-13/14/16/17/29)
# --------------------------------------------------------------------------


async def update_todo(todo_id: int, body: schemas.TodoUpdate) -> schemas.TodoCreated:
    fields = body.model_dump(exclude_unset=True)
    async with SessionLocal() as session:
        todo = await session.get(Todo, todo_id)
        if todo is None:
            raise TodoNotFound

        event = (
            await session.get(Event, todo.event_id)
            if todo.event_id is not None
            else None
        )

        had_start = todo.start_at is not None
        old_start = todo.start_at

        if "title" in fields:
            todo.title = fields["title"]
            if event is not None:
                event.title = fields["title"]
        if "all_day" in fields:
            todo.all_day = fields["all_day"]
            if event is not None:
                event.all_day = fields["all_day"]
        if "end_at" in fields:
            todo.end_at = fields["end_at"]
            if event is not None:
                event.end_at = fields["end_at"]
        if "done" in fields:
            todo.done = fields["done"]
            todo.completed_at = datetime.now(UTC) if fields["done"] else None

        reminders_set = "reminders" in fields
        # ``model_dump(exclude_unset=True)`` yields plain dicts for nested models;
        # re-wrap them as ``ReminderIn`` so ``_apply_reminders`` can read
        # ``.lead_time`` (it is shared with the create path, which passes models).
        raw_reminders = fields.get("reminders")
        reminders_in: list[schemas.ReminderIn] | None = (
            [schemas.ReminderIn.model_validate(r) for r in raw_reminders]
            if raw_reminders is not None
            else None
        )

        # --- start_at transitions drive the event + reminder lifecycle ---
        if "start_at" in fields:
            new_start = fields["start_at"]
            todo.start_at = new_start

            if new_start is None and had_start:
                # Dated → floating: delete owned event + clear pending reminders;
                # the todo survives (FR-16/17).
                await reminder_service.clear_pending_reminders(session, todo_id=todo.id)
                if event is not None:
                    todo.event_id = None
                    await session.flush()
                    await session.delete(event)
                    event = None
            elif new_start is not None and not had_start:
                # Floating → dated: create+link event, schedule reminders
                # (explicit set if provided, else default).
                event = await _create_event_for_todo(session, todo)
                await session.flush()
                await _apply_reminders(
                    session,
                    todo,
                    event,
                    reminders_in if reminders_set else None,
                )
                reminders_set = False  # consumed
            elif new_start is not None and had_start:
                if new_start != old_start and event is not None:
                    event.start_at = new_start
                if reminders_set:
                    # Replace the reminder set against the (new) due time.
                    await reminder_service.clear_pending_reminders(
                        session, todo_id=todo.id
                    )
                    await _apply_reminders(session, todo, event, reminders_in)
                    reminders_set = False  # consumed
                elif new_start != old_start:
                    # Due-time change only: recompute pending reminders (FR-29).
                    await reminder_service.recompute_pending_fire_times(
                        session, todo_id=todo.id, new_due=new_start
                    )

        # --- explicit reminder replacement without a start_at change (FR-28) ---
        if reminders_set:
            if todo.start_at is None:
                raise ValueError("reminders require a dated todo")
            await reminder_service.clear_pending_reminders(session, todo_id=todo.id)
            await _apply_reminders(session, todo, event, reminders_in)

        await session.commit()

        reminders = await reminder_service.list_pending_for_todo(
            session, todo_id=todo.id
        )
        await session.refresh(todo)
        if event is not None:
            await session.refresh(event)
        return _todo_created(todo, event, reminders)


# --------------------------------------------------------------------------
# Delete with service-level cascade (FR-15/16)
# --------------------------------------------------------------------------


async def delete_todo(todo_id: int) -> None:
    async with SessionLocal() as session:
        todo = await session.get(Todo, todo_id)
        if todo is None:
            raise TodoNotFound
        event = (
            await session.get(Event, todo.event_id)
            if todo.event_id is not None
            else None
        )
        # Break the FK, delete the todo (reminders cascade via DB), then the
        # owned event (service-level cascade — FK points todo→event).
        todo.event_id = None
        await session.flush()
        await session.delete(todo)
        if event is not None:
            await session.delete(event)
        await session.commit()


# --------------------------------------------------------------------------
# Events range query (FR-18/20)
# --------------------------------------------------------------------------


async def list_events(window_from: datetime, window_to: datetime) -> schemas.EventList:
    async with SessionLocal() as session:
        events = (
            await session.scalars(
                select(Event)
                .where(
                    Event.start_at <= window_to,
                    or_(
                        Event.end_at >= window_from,
                        (Event.end_at.is_(None)) & (Event.start_at >= window_from),
                    ),
                )
                .order_by(Event.start_at.asc(), Event.id.asc())
            )
        ).all()

        # Derive source todo_id per event (events.id ← todos.event_id).
        event_ids = [e.id for e in events]
        todo_map: dict[int, int] = {}
        if event_ids:
            rows = await session.execute(
                select(Todo.event_id, Todo.id).where(Todo.event_id.in_(event_ids))
            )
            todo_map = {ev_id: t_id for ev_id, t_id in rows.all()}

        items = [_event_out(e, todo_id=todo_map.get(e.id)) for e in events]
    return schemas.EventList(items=[i for i in items if i is not None])


# --------------------------------------------------------------------------
# Parse-and-create (FR-1..FR-10, FR-27)
# --------------------------------------------------------------------------


@dataclass(slots=True)
class _ParsedFields:
    title: str
    start_at: datetime | None
    all_day: bool
    lead_time: timedelta | None
    lead_source: str
    assumptions: list[schemas.Assumption]


async def _resolve_extraction(extraction, now_local: datetime) -> _ParsedFields:
    """Run the deterministic resolver off the event loop (NFR-6)."""
    resolved = await asyncio.to_thread(
        resolve_date, extraction.date_expr, now=now_local
    )
    lead = await asyncio.to_thread(
        resolve_lead_time, extraction.reminder_expr, now=now_local
    )

    assumptions: list[schemas.Assumption] = []

    if extraction.date_expr and resolved.start_at is None:
        # SLM gave a date phrase the resolver couldn't handle → ambiguous (FR-9a).
        raise ParseAmbiguous(
            schemas.ParseCandidate(
                title=extraction.title,
                date_expr=extraction.date_expr,
                reminder_expr=extraction.reminder_expr,
                start_at=None,
                end_at=None,
                all_day=None,
            )
        )

    if resolved.start_at is None:
        assumptions.append(
            schemas.Assumption(
                field="start_at",
                value="null",
                reason="no resolvable time → floating (undated) task",
            )
        )
        return _ParsedFields(
            title=extraction.title,
            start_at=None,
            all_day=False,
            lead_time=None,
            lead_source="default",
            assumptions=assumptions,
        )

    if resolved.date_only:
        assumptions.append(
            schemas.Assumption(
                field="all_day",
                value="true",
                reason="a date but no time was given → treated as all-day",
            )
        )

    if lead is None:
        lead = settings.REMINDER_DEFAULT_LEAD
        lead_source = "default"
        assumptions.append(
            schemas.Assumption(
                field="lead_time",
                value=_iso_duration(lead),
                reason="no reminder stated → default lead time applied",
            )
        )
    else:
        lead_source = "parsed"

    return _ParsedFields(
        title=extraction.title,
        start_at=resolved.start_at,
        all_day=resolved.all_day,
        lead_time=lead,
        lead_source=lead_source,
        assumptions=assumptions,
    )


def _iso_duration(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    out = "P"
    if days:
        out += f"{days}D"
    if hours or minutes or seconds:
        out += "T"
        if hours:
            out += f"{hours}H"
        if minutes:
            out += f"{minutes}M"
        if seconds:
            out += f"{seconds}S"
    return out if out != "P" else "PT0S"


async def parse_and_create(text: str, parser: Parser) -> schemas.ParseResult:
    """Headline NL capture. Raises ParserError subclasses (→ 503) or
    ParseAmbiguous (→ 422); never persists on failure (FR-10, AC-3)."""
    now_local = datetime.now(ZoneInfo(settings.PLANNER_RESOLVE_TZ))
    try:
        extraction = await parser.extract(text, now=now_local)
    except ParserError:
        raise
    except Exception as exc:
        logger.warning("parser raised unexpectedly", exc_info=True)
        raise ParserUnavailable("parser error") from exc

    parsed = await _resolve_extraction(extraction, now_local)

    async with SessionLocal() as session:
        todo = Todo(
            title=parsed.title,
            source_text=text,
            start_at=parsed.start_at,
            all_day=parsed.all_day,
        )
        session.add(todo)
        await session.flush()

        event: Event | None = None
        reminders: list[Reminder] = []
        if parsed.start_at is not None:
            event = await _create_event_for_todo(session, todo)
            await session.flush()
            lead_specs = [(parsed.lead_time, parsed.lead_source)]
            reminders = await reminder_service.schedule_reminders(
                session,
                todo_id=todo.id,
                event_id=event.id,
                due=parsed.start_at,
                lead_specs=lead_specs,
            )

        await session.commit()
        await session.refresh(todo)
        if event is not None:
            await session.refresh(event)
        for r in reminders:
            await session.refresh(r)

        return schemas.ParseResult(
            todo=schemas.TodoOut.model_validate(todo),
            event=_event_out(event, todo_id=todo.id),
            reminders=[schemas.ReminderOut.model_validate(r) for r in reminders],
            assumptions=parsed.assumptions,
        )


# --------------------------------------------------------------------------
# In-app reminder feed + acknowledge (FR-31a)
# --------------------------------------------------------------------------


async def reminder_feed(limit: int) -> schemas.ReminderFeed:
    late_threshold = timedelta(seconds=2 * settings.REMINDER_SCAN_INTERVAL_S)
    async with SessionLocal() as session:
        rows = await session.execute(
            select(Reminder, ReminderDelivery.delivered_at, Todo.title, Todo.start_at)
            .join(
                ReminderDelivery,
                (ReminderDelivery.reminder_id == Reminder.id)
                & (ReminderDelivery.channel == "in_app")
                & (ReminderDelivery.status == "sent"),
            )
            .join(Todo, Todo.id == Reminder.todo_id)
            .where(Reminder.acknowledged_at.is_(None))
            .order_by(ReminderDelivery.delivered_at.asc())
            .limit(limit)
        )
        items = []
        for reminder, delivered_at, title, start_at in rows.all():
            is_late = (
                delivered_at is not None
                and (delivered_at - reminder.fire_at) > late_threshold
            )
            items.append(
                schemas.ReminderFeedItem(
                    id=reminder.id,
                    todo_id=reminder.todo_id,
                    title=title,
                    fire_at=reminder.fire_at,
                    delivered_at=delivered_at,
                    start_at=start_at,
                    is_late=is_late,
                )
            )
    return schemas.ReminderFeed(items=items)


async def acknowledge_reminder(reminder_id: int) -> schemas.ReminderOut:
    async with SessionLocal() as session:
        reminder = await session.get(Reminder, reminder_id)
        if reminder is None:
            raise ReminderNotFound
        if reminder.status == "pending":
            raise ReminderNotFired
        if reminder.acknowledged_at is None:
            reminder.acknowledged_at = datetime.now(UTC)
            reminder.status = "acknowledged"
            await session.commit()
            await session.refresh(reminder)
        return schemas.ReminderOut.model_validate(reminder)


# --------------------------------------------------------------------------
# Retention: daily prune of fired reminders (NFR-23)
# --------------------------------------------------------------------------


async def clean_fired_reminders(retention_days: int | None = None) -> int:
    from sqlalchemy import delete

    days = (
        retention_days
        if retention_days is not None
        else settings.REMINDER_RETENTION_DAYS
    )
    cutoff = datetime.now(UTC) - timedelta(days=days)
    async with SessionLocal() as session:
        result = await session.execute(
            delete(Reminder).where(
                Reminder.status.in_(("sent", "acknowledged", "expired")),
                Reminder.fire_at < cutoff,
            )
        )
        await session.commit()
    return result.rowcount
