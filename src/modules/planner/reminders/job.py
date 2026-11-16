"""Background reminder scan + dispatch (FR-32/33/34, NFR-18/19).

``scan_due_reminders`` is registered on the EXISTING shared ``AsyncIOScheduler``
as an interval job. It:

1. Selects ``pending`` reminders whose ``fire_at <= now`` (the indexed scan on
   ``(status, fire_at)``) plus stale ``sending`` rows past the claim grace
   (crash recovery).
2. Catch-up cutoff: reminders more than ``REMINDER_CATCHUP_WINDOW`` late are
   transitioned to ``expired`` and NOT delivered (FR-34, OQ-13).
3. Claims each row with a guarded UPDATE ``pending→sending`` (idempotency gate),
   fans it out to every channel, then records ``sent`` + per-channel outcomes.

Exactly-once is persisted via the ``status`` transition; a crash mid-dispatch
leaves ``sending`` which the next scan re-claims after the grace window.
Everything is awaited so the loop (shared with the 5s sampler) is never blocked.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import SessionLocal

from ..models import Reminder, Todo
from .channels import CHANNELS
from .delivery import build_message

logger = logging.getLogger(__name__)

_SCAN_LIMIT = 200


async def _select_due(session: AsyncSession, now: datetime) -> list[Reminder]:
    stale_cutoff = now - settings.REMINDER_CLAIM_GRACE
    result = await session.scalars(
        select(Reminder)
        .where(
            Reminder.fire_at <= now,
            or_(
                Reminder.status == "pending",
                (Reminder.status == "sending") & (Reminder.claimed_at < stale_cutoff),
            ),
        )
        .order_by(Reminder.fire_at.asc())
        .limit(_SCAN_LIMIT)
    )
    return list(result.all())


async def _claim(session: AsyncSession, reminder: Reminder, now: datetime) -> bool:
    """Guarded claim: pending|stale-sending → sending. Returns True if won."""
    stale_cutoff = now - settings.REMINDER_CLAIM_GRACE
    result = await session.execute(
        update(Reminder)
        .where(
            Reminder.id == reminder.id,
            or_(
                Reminder.status == "pending",
                (Reminder.status == "sending") & (Reminder.claimed_at < stale_cutoff),
            ),
        )
        .values(status="sending", claimed_at=now)
    )
    await session.commit()
    return result.rowcount == 1


async def _dispatch_one(
    session: AsyncSession, reminder: Reminder, now: datetime
) -> None:
    title = await session.scalar(select(Todo.title).where(Todo.id == reminder.todo_id))
    # Late = delivered meaningfully after the scan cadence (e.g. restart catch-up).
    late_threshold = timedelta(seconds=2 * settings.REMINDER_SCAN_INTERVAL_S)
    is_late = (now - reminder.fire_at) > late_threshold
    text = build_message(reminder, title=title or "task", is_late=is_late)

    any_success = False
    for channel in CHANNELS:
        try:
            result = await channel.deliver(session, reminder, text=text)
            if result.ok:
                any_success = True
        except Exception:
            logger.exception("channel %s raised during delivery", channel.name)

    if any_success:
        # At least one channel delivered — reminder is done (exactly-once).
        reminder.status = "sent"
        reminder.sent_at = datetime.now(UTC)
    else:
        # Nothing delivered (incl. the reliable in-app channel). Leave the row
        # as 'sending' so the stale-claim path re-claims and retries it after
        # the grace window, rather than silently marking it sent (M-1).
        logger.warning(
            "reminder %s: no channel delivered; leaving for retry", reminder.id
        )
    await session.commit()


async def scan_due_reminders() -> int:
    """Scan + dispatch due reminders. Returns the number dispatched."""
    now = datetime.now(UTC)
    dispatched = 0
    async with SessionLocal() as session:
        due = await _select_due(session, now)

        for reminder in due:
            # Catch-up cutoff: too-late reminders expire, not deliver (FR-34).
            lateness = now - reminder.fire_at
            if lateness > settings.REMINDER_CATCHUP_WINDOW:
                await session.execute(
                    update(Reminder)
                    .where(Reminder.id == reminder.id, Reminder.status == "pending")
                    .values(status="expired")
                )
                await session.commit()
                logger.info(
                    "reminder %s expired (%.0fs late)",
                    reminder.id,
                    lateness.total_seconds(),
                )
                continue

            if not await _claim(session, reminder, now):
                continue
            await _dispatch_one(session, reminder, now)
            dispatched += 1

    if dispatched:
        logger.info("dispatched %d reminder(s)", dispatched)
    return dispatched
