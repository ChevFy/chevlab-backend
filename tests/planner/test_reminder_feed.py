"""In-app reminder feed + acknowledge (FR-30/31a, §5.2/5.3) and retention prune.

The feed surfaces reminders that fired to the in-app channel and are not yet
acknowledged. ``ack`` is idempotent; ack-ing a never-fired reminder is a 409.
"""

from datetime import UTC, datetime, timedelta

import pytest

import modules.notify.service as notify_service
from modules.planner.models import Reminder, Todo
from modules.planner.reminders.job import scan_due_reminders
from modules.planner.service import clean_fired_reminders


@pytest.fixture
def telegram_ok(monkeypatch):
    async def _send(text: str) -> bool:
        return True

    monkeypatch.setattr(notify_service, "send_message", _send)


async def _fire_one(session_local, *, fire_at=None):
    fire_at = fire_at or datetime.now(UTC) - timedelta(minutes=1)
    async with session_local() as s:
        todo = Todo(title="feed-task", start_at=fire_at + timedelta(days=1))
        s.add(todo)
        await s.flush()
        rem = Reminder(
            todo_id=todo.id,
            lead_time=timedelta(days=1),
            lead_source="default",
            fire_at=fire_at,
            status="pending",
        )
        s.add(rem)
        await s.commit()
        await s.refresh(rem)
        rid = rem.id
    await scan_due_reminders()
    return rid


@pytest.mark.asyncio
async def test_feed_lists_fired_unacknowledged(client, session_local, telegram_ok):
    rid = await _fire_one(session_local)
    resp = client.get("/api/planner/reminders")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == rid
    assert items[0]["title"] == "feed-task"
    assert items[0]["delivered_at"] is not None


@pytest.mark.asyncio
async def test_feed_empty_before_firing(client, session_local, telegram_ok):
    # pending (not fired) reminder should NOT appear
    fire_at = datetime.now(UTC) + timedelta(days=1)
    async with session_local() as s:
        todo = Todo(title="t", start_at=fire_at + timedelta(days=1))
        s.add(todo)
        await s.flush()
        s.add(
            Reminder(
                todo_id=todo.id,
                lead_time=timedelta(days=1),
                lead_source="default",
                fire_at=fire_at,
                status="pending",
            )
        )
        await s.commit()
    assert client.get("/api/planner/reminders").json()["items"] == []


@pytest.mark.asyncio
async def test_ack_removes_from_feed(client, session_local, telegram_ok):
    rid = await _fire_one(session_local)
    ack = client.post(f"/api/planner/reminders/{rid}/ack")
    assert ack.status_code == 200
    assert ack.json()["status"] == "acknowledged"
    assert ack.json()["acknowledged_at"] is not None
    # gone from feed
    assert client.get("/api/planner/reminders").json()["items"] == []


@pytest.mark.asyncio
async def test_ack_is_idempotent(client, session_local, telegram_ok):
    rid = await _fire_one(session_local)
    first = client.post(f"/api/planner/reminders/{rid}/ack")
    second = client.post(f"/api/planner/reminders/{rid}/ack")
    assert first.status_code == 200
    assert second.status_code == 200  # idempotent, not an error
    assert second.json()["status"] == "acknowledged"


def test_ack_not_found(client):
    assert client.post("/api/planner/reminders/99999/ack").status_code == 404


@pytest.mark.asyncio
async def test_ack_pending_reminder_conflicts(client, session_local):
    """409 only when ack-ing a reminder that never fired (still pending)."""
    async with session_local() as s:
        todo = Todo(title="t", start_at=datetime.now(UTC) + timedelta(days=2))
        s.add(todo)
        await s.flush()
        rem = Reminder(
            todo_id=todo.id,
            lead_time=timedelta(days=1),
            lead_source="default",
            fire_at=datetime.now(UTC) + timedelta(days=1),
            status="pending",
        )
        s.add(rem)
        await s.commit()
        await s.refresh(rem)
        rid = rem.id
    resp = client.post(f"/api/planner/reminders/{rid}/ack")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Reminder has not fired yet"


@pytest.mark.asyncio
async def test_late_reminder_flagged_in_feed(client, session_local, telegram_ok):
    """FR-34: a reminder delivered well after fire-time is marked is_late."""
    from core.config import settings

    late_fire = datetime.now(UTC) - (settings.REMINDER_CATCHUP_WINDOW // 2)
    await _fire_one(session_local, fire_at=late_fire)
    items = client.get("/api/planner/reminders").json()["items"]
    assert len(items) == 1
    assert items[0]["is_late"] is True


@pytest.mark.asyncio
async def test_feed_limit_bounds(client, session_local, telegram_ok):
    assert client.get("/api/planner/reminders?limit=0").status_code == 422
    assert client.get("/api/planner/reminders?limit=501").status_code == 422


# --- retention prune (NFR-23) ----------------------------------------------


@pytest.mark.asyncio
async def test_clean_fired_reminders_prunes_old_sent(session_local, telegram_ok):
    old_fire = datetime.now(UTC) - timedelta(days=100)
    async with session_local() as s:
        todo = Todo(title="old", start_at=old_fire + timedelta(days=1))
        s.add(todo)
        await s.flush()
        s.add(
            Reminder(
                todo_id=todo.id,
                lead_time=timedelta(days=1),
                lead_source="default",
                fire_at=old_fire,
                status="sent",
                sent_at=old_fire,
            )
        )
        await s.commit()
    removed = await clean_fired_reminders(retention_days=30)
    assert removed == 1


@pytest.mark.asyncio
async def test_clean_keeps_recent_and_pending(session_local):
    async with session_local() as s:
        todo = Todo(title="recent", start_at=datetime.now(UTC) + timedelta(days=1))
        s.add(todo)
        await s.flush()
        s.add(
            Reminder(
                todo_id=todo.id,
                lead_time=timedelta(days=1),
                lead_source="default",
                fire_at=datetime.now(UTC) + timedelta(days=1),
                status="pending",
            )
        )
        await s.commit()
    removed = await clean_fired_reminders(retention_days=30)
    assert removed == 0
