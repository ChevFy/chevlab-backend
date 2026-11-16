"""Background reminder firing job (job.scan_due_reminders) — stage-05 hot spot #1.

Covers the exactly-once guarantees (AC-9/13/14, FR-32/33/34, NFR-18):
  * guarded pending→sending→sent claim; fires exactly once across repeated scans
  * stale-'sending' crash re-claim after REMINDER_CLAIM_GRACE
  * catch-up 'expired' cutoff for reminders too late to deliver
  * survives a simulated restart (persisted state → no loss, no double fire)
  * concurrent scans do not double-dispatch
  * both channels fire; Telegram unconfigured → in-app still records (NFR-22)

The ``Parser`` is irrelevant here (no HTTP). ``notify.send_message`` is always
stubbed so no Telegram network call is made.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

import modules.notify.service as notify_service
from core.config import settings
from modules.planner.models import Reminder, ReminderDelivery, Todo
from modules.planner.reminders.job import scan_due_reminders


@pytest.fixture
def telegram_ok(monkeypatch):
    """Stub notify.send_message → success, recording calls."""
    calls = []

    async def _send(text: str) -> bool:
        calls.append(text)
        return True

    monkeypatch.setattr(notify_service, "send_message", _send)
    return calls


@pytest.fixture
def telegram_down(monkeypatch):
    """Stub notify.send_message → failure (unconfigured/unreachable)."""
    calls = []

    async def _send(text: str) -> bool:
        calls.append(text)
        return False

    monkeypatch.setattr(notify_service, "send_message", _send)
    return calls


async def _make_reminder(session_local, *, fire_at, status="pending", lead=None):
    """Insert a todo+reminder directly, returning the reminder id."""
    lead = lead or timedelta(days=1)
    async with session_local() as s:
        todo = Todo(title="task", start_at=fire_at + lead, all_day=False)
        s.add(todo)
        await s.flush()
        rem = Reminder(
            todo_id=todo.id,
            lead_time=lead,
            lead_source="default",
            fire_at=fire_at,
            status=status,
        )
        s.add(rem)
        await s.commit()
        await s.refresh(rem)
        return rem.id


async def _get(session_local, rem_id):
    async with session_local() as s:
        return await s.get(Reminder, rem_id)


async def _delivery_count(session_local, rem_id, *, channel=None, status="sent"):
    async with session_local() as s:
        stmt = (
            select(func.count())
            .select_from(ReminderDelivery)
            .where(ReminderDelivery.reminder_id == rem_id)
            .where(ReminderDelivery.status == status)
        )
        if channel:
            stmt = stmt.where(ReminderDelivery.channel == channel)
        return await s.scalar(stmt)


# --- exactly once ----------------------------------------------------------


@pytest.mark.asyncio
async def test_due_reminder_fires_once_both_channels(session_local, telegram_ok):
    rem_id = await _make_reminder(
        session_local, fire_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    dispatched = await scan_due_reminders()
    assert dispatched == 1

    rem = await _get(session_local, rem_id)
    assert rem.status == "sent"
    assert rem.sent_at is not None
    # both channels delivered
    assert await _delivery_count(session_local, rem_id, channel="telegram") == 1
    assert await _delivery_count(session_local, rem_id, channel="in_app") == 1
    assert len(telegram_ok) == 1  # exactly one Telegram send


@pytest.mark.asyncio
async def test_repeated_scans_do_not_refire(session_local, telegram_ok):
    rem_id = await _make_reminder(
        session_local, fire_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    first = await scan_due_reminders()
    second = await scan_due_reminders()
    third = await scan_due_reminders()
    assert first == 1
    assert second == 0 and third == 0
    assert len(telegram_ok) == 1  # never re-sent
    assert await _delivery_count(session_local, rem_id, channel="telegram") == 1


@pytest.mark.asyncio
async def test_future_reminder_not_fired(session_local, telegram_ok):
    rem_id = await _make_reminder(
        session_local, fire_at=datetime.now(UTC) + timedelta(hours=1)
    )
    assert await scan_due_reminders() == 0
    assert (await _get(session_local, rem_id)).status == "pending"


@pytest.mark.asyncio
async def test_concurrent_scans_fire_once(session_local, telegram_ok):
    """AC-13/NFR-18: two overlapping scans must not double-dispatch.

    The guarded ``pending→sending`` UPDATE is the idempotency gate.
    """
    import asyncio

    rem_id = await _make_reminder(
        session_local, fire_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    results = await asyncio.gather(scan_due_reminders(), scan_due_reminders())
    assert sum(results) == 1, f"expected exactly one dispatch, got {results}"
    assert len(telegram_ok) == 1
    assert (await _get(session_local, rem_id)).status == "sent"


# --- stale 'sending' crash re-claim ----------------------------------------


@pytest.mark.asyncio
async def test_stale_sending_reclaimed_after_grace(session_local, telegram_ok):
    """A crash mid-dispatch leaves 'sending'; after the grace window the next
    scan re-claims and completes it (FR-33, NFR-18)."""
    now = datetime.now(UTC)
    rem_id = await _make_reminder(
        session_local, fire_at=now - timedelta(minutes=10), status="sending"
    )
    # Simulate a stale claim older than the grace window.
    async with session_local() as s:
        rem = await s.get(Reminder, rem_id)
        rem.claimed_at = now - settings.REMINDER_CLAIM_GRACE - timedelta(minutes=1)
        await s.commit()

    dispatched = await scan_due_reminders()
    assert dispatched == 1
    assert (await _get(session_local, rem_id)).status == "sent"


@pytest.mark.asyncio
async def test_fresh_sending_not_reclaimed(session_local, telegram_ok):
    """A recently-claimed 'sending' row (another worker in flight) is skipped."""
    now = datetime.now(UTC)
    rem_id = await _make_reminder(
        session_local, fire_at=now - timedelta(minutes=1), status="sending"
    )
    async with session_local() as s:
        rem = await s.get(Reminder, rem_id)
        rem.claimed_at = now  # fresh claim
        await s.commit()

    assert await scan_due_reminders() == 0
    assert (await _get(session_local, rem_id)).status == "sending"


# --- catch-up / expiry (FR-34) ---------------------------------------------


@pytest.mark.asyncio
async def test_too_late_reminder_expires_not_delivered(session_local, telegram_ok):
    now = datetime.now(UTC)
    rem_id = await _make_reminder(
        session_local,
        fire_at=now - settings.REMINDER_CATCHUP_WINDOW - timedelta(hours=1),
    )
    dispatched = await scan_due_reminders()
    assert dispatched == 0
    assert (await _get(session_local, rem_id)).status == "expired"
    assert len(telegram_ok) == 0  # never delivered


@pytest.mark.asyncio
async def test_within_catchup_window_delivered_as_late(session_local, telegram_ok):
    """AC-14: a reminder missed during downtime but within the window is still
    delivered (flagged late)."""
    now = datetime.now(UTC)
    # Late enough to count as 'late' but inside the catch-up window.
    rem_id = await _make_reminder(
        session_local, fire_at=now - (settings.REMINDER_CATCHUP_WINDOW // 2)
    )
    dispatched = await scan_due_reminders()
    assert dispatched == 1
    assert (await _get(session_local, rem_id)).status == "sent"
    assert len(telegram_ok) == 1


# --- restart survival ------------------------------------------------------


@pytest.mark.asyncio
async def test_survives_restart_fires_once(session_local, telegram_ok):
    """AC-13: reminder persisted while 'down', then fire-time arrives → fires once.

    The in-memory DB IS the persisted state; 'restart' = a later scan. The row
    survives and fires exactly once, not lost, not duplicated.
    """
    # Scheduled in the past (as if fire-time passed just after a restart).
    rem_id = await _make_reminder(
        session_local, fire_at=datetime.now(UTC) - timedelta(minutes=2)
    )
    # "restart": the first scan after boot
    assert await scan_due_reminders() == 1
    # subsequent scans (steady state) never re-fire
    assert await scan_due_reminders() == 0
    assert (await _get(session_local, rem_id)).status == "sent"
    assert len(telegram_ok) == 1


# --- graceful degradation: Telegram down (NFR-22) --------------------------


@pytest.mark.asyncio
async def test_telegram_down_inapp_still_recorded(session_local, telegram_down):
    """NFR-22: Telegram unconfigured/unreachable → reminder still fires in-app,
    the in-app delivery row is recorded, and the reminder is marked sent."""
    rem_id = await _make_reminder(
        session_local, fire_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    dispatched = await scan_due_reminders()
    assert dispatched == 1
    # reminder still completes
    assert (await _get(session_local, rem_id)).status == "sent"
    # in-app delivery recorded as sent
    assert (
        await _delivery_count(session_local, rem_id, channel="in_app", status="sent")
        == 1
    )
    # telegram delivery recorded as failed (independent failure)
    assert (
        await _delivery_count(
            session_local, rem_id, channel="telegram", status="failed"
        )
        == 1
    )


@pytest.mark.asyncio
async def test_channel_exception_does_not_block_others(session_local, monkeypatch):
    """If one channel raises, the job logs and continues (other channel + sent)."""

    async def _boom(text: str) -> bool:
        raise RuntimeError("telegram exploded")

    monkeypatch.setattr(notify_service, "send_message", _boom)
    rem_id = await _make_reminder(
        session_local, fire_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    dispatched = await scan_due_reminders()
    assert dispatched == 1
    assert (await _get(session_local, rem_id)).status == "sent"
    assert await _delivery_count(session_local, rem_id, channel="in_app") == 1
