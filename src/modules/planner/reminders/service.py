"""Reminder scheduling helpers (compute/recompute fire-times).

Operates on a *provided* session so todo + event + reminder changes commit in one
transaction (the caller owns the transaction). ``fire_at = due - lead_time`` is
computed at write time and stored as a UTC instant (the scan key).
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings

from ..models import Reminder


def _lead_specs_from_ins(
    reminders_in: list[timedelta] | None,
) -> list[tuple[timedelta, str]]:
    """Map incoming lead-times to (lead_time, lead_source) tuples.

    ``None`` → apply the configured default (FR-24/25). ``[]`` → no reminders
    (explicit clear, FR-28). A non-empty list → ``manual`` provenance.
    """
    if reminders_in is None:
        return [(settings.REMINDER_DEFAULT_LEAD, "default")]
    return [(lt, "manual") for lt in reminders_in]


async def schedule_reminders(
    session: AsyncSession,
    *,
    todo_id: int,
    event_id: int | None,
    due: datetime,
    lead_specs: list[tuple[timedelta, str]],
) -> list[Reminder]:
    """Create reminder rows for a dated item (FR-23/24)."""
    created: list[Reminder] = []
    for lead_time, lead_source in lead_specs:
        reminder = Reminder(
            todo_id=todo_id,
            event_id=event_id,
            lead_time=lead_time,
            lead_source=lead_source,
            fire_at=due - lead_time,
            status="pending",
        )
        session.add(reminder)
        created.append(reminder)
    return created


async def clear_pending_reminders(session: AsyncSession, *, todo_id: int) -> None:
    """Delete only the not-yet-fired reminders for a todo (FR-28/29).

    Already ``sent``/``acknowledged``/``failed``/``expired`` reminders are left
    untouched so they neither re-fire nor vanish from history.
    """
    result = await session.scalars(
        select(Reminder).where(
            Reminder.todo_id == todo_id,
            Reminder.status.in_(("pending", "sending")),
        )
    )
    for reminder in result.all():
        await session.delete(reminder)


async def recompute_pending_fire_times(
    session: AsyncSession, *, todo_id: int, new_due: datetime
) -> None:
    """Recompute pending reminders against a changed due time (FR-29).

    ``sent``/``acknowledged`` reminders are not touched (the status gate prevents
    a re-fire).
    """
    result = await session.scalars(
        select(Reminder).where(
            Reminder.todo_id == todo_id,
            Reminder.status.in_(("pending", "sending")),
        )
    )
    for reminder in result.all():
        reminder.fire_at = new_due - reminder.lead_time


async def list_pending_for_todo(
    session: AsyncSession, *, todo_id: int
) -> list[Reminder]:
    result = await session.scalars(
        select(Reminder)
        .where(Reminder.todo_id == todo_id)
        .order_by(Reminder.fire_at.asc())
    )
    return list(result.all())
