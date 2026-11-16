"""End-to-end default-reminder user stories (AC-9/11/12).

Create a todo over HTTP, then run the firing job directly, asserting the whole
chain: default lead scheduled → fires once → both channels recorded → surfaces in
the in-app feed. Also covers the settings-configurable default (AC-11) and the
floating-task-has-no-reminder rule at the firing level (AC-12).
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

import modules.notify.service as notify_service
from core.config import settings
from modules.planner.models import Reminder
from modules.planner.reminders.job import scan_due_reminders


@pytest.fixture
def telegram_calls(monkeypatch):
    calls = []

    async def _send(text: str) -> bool:
        calls.append(text)
        return True

    monkeypatch.setattr(notify_service, "send_message", _send)
    return calls


@pytest.mark.asyncio
async def test_ac9_default_reminder_fires_once_both_channels(
    client, session_local, telegram_calls
):
    """AC-9: dated todo, no reminder stated → one default reminder → fires once to
    both Telegram and in-app; not re-dispatched on the next scan."""
    # Due 25h from now so the default 1-day reminder fires ~1h from now... instead
    # place due such that fire_at is already in the (recent) past to trigger now.
    due = datetime.now(UTC) + timedelta(days=1) - timedelta(minutes=1)
    created = client.post(
        "/api/planner/todos", json={"title": "pay rent", "start_at": due.isoformat()}
    ).json()
    rem = created["reminders"][0]
    assert rem["lead_source"] == "default"
    assert rem["lead_time"] == "P1D"

    # fire_at = due - 1 day ≈ 1 minute ago → due now.
    first = await scan_due_reminders()
    assert first == 1
    second = await scan_due_reminders()
    assert second == 0  # not re-dispatched

    assert len(telegram_calls) == 1  # exactly one Telegram send

    # in-app surfaced in the feed
    feed = client.get("/api/planner/reminders").json()["items"]
    assert len(feed) == 1
    assert feed[0]["todo_id"] == created["todo"]["id"]
    assert feed[0]["title"] == "pay rent"


@pytest.mark.asyncio
async def test_ac11_configurable_default_lead(
    client, session_local, telegram_calls, monkeypatch
):
    """AC-11: changing REMINDER_DEFAULT_LEAD via settings changes the scheduled
    lead with no code change."""
    monkeypatch.setattr(settings, "REMINDER_DEFAULT_LEAD", timedelta(hours=2))
    due = datetime.now(UTC) + timedelta(days=5)
    created = client.post(
        "/api/planner/todos", json={"title": "x", "start_at": due.isoformat()}
    ).json()
    rem = created["reminders"][0]
    assert rem["lead_source"] == "default"
    assert rem["lead_time"] == "PT2H"
    fire = datetime.fromisoformat(rem["fire_at"])
    assert fire == due - timedelta(hours=2)


@pytest.mark.asyncio
async def test_ac12_floating_task_has_no_reminder(
    client, session_local, telegram_calls
):
    """AC-12: a floating todo schedules no reminder and the scan fires nothing."""
    client.post("/api/planner/todos", json={"title": "someday"})
    async with session_local() as s:
        count = await s.scalar(select(func.count()).select_from(Reminder))
    assert count == 0
    assert await scan_due_reminders() == 0
    assert len(telegram_calls) == 0
