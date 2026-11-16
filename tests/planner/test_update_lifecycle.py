"""update_todo link lifecycle (FR-13/14/16/17/29) — stage-05 hot spot #3.

Exercises the four transitions the service must get right:
  * floating → dated  (event + reminder created)
  * dated → floating  (event deleted + pending reminders cleared, todo survives)
  * due-time change    (pending reminders recomputed; sent ones untouched)
  * explicit reminders:[] clears
plus done/reopen (FR-14).

Some assertions reach past the HTTP layer into the DB via the service's own
session to confirm the event row / reminder rows actually changed (not just the
response projection).
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from modules.planner.models import Event, Reminder

NOW = datetime.now(UTC).replace(microsecond=0)
DUE = NOW + timedelta(days=10)
DUE_ISO = DUE.isoformat()


async def _count(session_local, model, **filters):
    async with session_local() as s:
        stmt = select(func.count()).select_from(model)
        for k, v in filters.items():
            stmt = stmt.where(getattr(model, k) == v)
        return await s.scalar(stmt)


# --- floating → dated ------------------------------------------------------


@pytest.mark.asyncio
async def test_floating_to_dated_creates_event_and_reminder(client, session_local):
    tid = client.post("/api/planner/todos", json={"title": "task"}).json()["todo"]["id"]
    assert await _count(session_local, Event) == 0

    resp = client.patch(f"/api/planner/todos/{tid}", json={"start_at": DUE_ISO})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["event"] is not None
    assert body["todo"]["event_id"] == body["event"]["id"]
    # default reminder created
    assert len(body["reminders"]) == 1
    assert body["reminders"][0]["lead_source"] == "default"
    assert await _count(session_local, Event) == 1
    assert await _count(session_local, Reminder, todo_id=tid) == 1


@pytest.mark.asyncio
async def test_floating_to_dated_with_explicit_reminders(client, session_local):
    tid = client.post("/api/planner/todos", json={"title": "t"}).json()["todo"]["id"]
    resp = client.patch(
        f"/api/planner/todos/{tid}",
        json={"start_at": DUE_ISO, "reminders": [{"lead_time": "PT3H"}]},
    )
    assert resp.status_code == 200
    rems = resp.json()["reminders"]
    assert len(rems) == 1
    assert rems[0]["lead_source"] == "manual"
    assert rems[0]["lead_time"] == "PT3H"


# --- dated → floating ------------------------------------------------------


@pytest.mark.asyncio
async def test_dated_to_floating_deletes_event_clears_reminders(client, session_local):
    created = client.post(
        "/api/planner/todos", json={"title": "t", "start_at": DUE_ISO}
    ).json()
    tid = created["todo"]["id"]
    assert await _count(session_local, Event) == 1
    assert await _count(session_local, Reminder, todo_id=tid) == 1

    resp = client.patch(f"/api/planner/todos/{tid}", json={"start_at": None})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # todo survives but is now floating
    assert body["todo"]["start_at"] is None
    assert body["todo"]["event_id"] is None
    assert body["event"] is None
    assert body["reminders"] == []
    # owned event deleted, pending reminder cleared in the DB
    assert await _count(session_local, Event) == 0
    assert await _count(session_local, Reminder, todo_id=tid) == 0
    # the todo itself is still present
    assert client.get(f"/api/planner/todos/{tid}").status_code == 200


# --- due-time change -------------------------------------------------------


@pytest.mark.asyncio
async def test_due_time_change_recomputes_pending_reminder(client, session_local):
    created = client.post(
        "/api/planner/todos", json={"title": "t", "start_at": DUE_ISO}
    ).json()
    tid = created["todo"]["id"]
    rem_id = created["reminders"][0]["id"]
    # move due 2 days later
    new_due = DUE + timedelta(days=2)
    resp = client.patch(
        f"/api/planner/todos/{tid}", json={"start_at": new_due.isoformat()}
    )
    assert resp.status_code == 200, resp.text
    # same reminder row, recomputed fire_at = new_due - 1 day
    async with session_local() as s:
        rem = await s.get(Reminder, rem_id)
        assert rem is not None, "pending reminder should be recomputed, not replaced"
        assert rem.fire_at == new_due - timedelta(days=1)
        assert rem.status == "pending"


@pytest.mark.asyncio
async def test_due_time_change_leaves_sent_reminder_untouched(client, session_local):
    """FR-29: already-sent reminders are NOT recomputed/cleared on a due change."""
    created = client.post(
        "/api/planner/todos", json={"title": "t", "start_at": DUE_ISO}
    ).json()
    tid = created["todo"]["id"]
    rem_id = created["reminders"][0]["id"]
    original_fire = datetime.fromisoformat(created["reminders"][0]["fire_at"])

    # Simulate that reminder already fired (sent).
    async with session_local() as s:
        rem = await s.get(Reminder, rem_id)
        rem.status = "sent"
        rem.sent_at = datetime.now(UTC)
        await s.commit()

    new_due = DUE + timedelta(days=5)
    resp = client.patch(
        f"/api/planner/todos/{tid}", json={"start_at": new_due.isoformat()}
    )
    assert resp.status_code == 200
    async with session_local() as s:
        rem = await s.get(Reminder, rem_id)
        assert rem.status == "sent"
        # fire_at unchanged — a sent reminder is history, not recomputed.
        assert rem.fire_at == original_fire


# --- explicit reminders replace / clear ------------------------------------


@pytest.mark.asyncio
async def test_explicit_empty_reminders_clears_pending(client, session_local):
    created = client.post(
        "/api/planner/todos", json={"title": "t", "start_at": DUE_ISO}
    ).json()
    tid = created["todo"]["id"]
    assert await _count(session_local, Reminder, todo_id=tid) == 1

    resp = client.patch(f"/api/planner/todos/{tid}", json={"reminders": []})
    assert resp.status_code == 200
    assert resp.json()["reminders"] == []
    assert await _count(session_local, Reminder, todo_id=tid) == 0


@pytest.mark.asyncio
async def test_explicit_reminders_replace_set(client, session_local):
    created = client.post(
        "/api/planner/todos", json={"title": "t", "start_at": DUE_ISO}
    ).json()
    tid = created["todo"]["id"]
    resp = client.patch(
        f"/api/planner/todos/{tid}",
        json={"reminders": [{"lead_time": "PT1H"}, {"lead_time": "PT6H"}]},
    )
    assert resp.status_code == 200
    rems = resp.json()["reminders"]
    assert len(rems) == 2
    assert {r["lead_time"] for r in rems} == {"PT1H", "PT6H"}
    assert all(r["lead_source"] == "manual" for r in rems)


def test_reminders_on_floating_todo_rejected(client):
    """FR-26: cannot set reminders on a todo that is/stays floating → 422."""
    tid = client.post("/api/planner/todos", json={"title": "t"}).json()["todo"]["id"]
    resp = client.patch(
        f"/api/planner/todos/{tid}", json={"reminders": [{"lead_time": "PT1H"}]}
    )
    assert resp.status_code == 422


# --- done / reopen ---------------------------------------------------------


def test_mark_done_and_reopen(client):
    tid = client.post("/api/planner/todos", json={"title": "t"}).json()["todo"]["id"]
    done = client.patch(f"/api/planner/todos/{tid}", json={"done": True}).json()
    assert done["todo"]["done"] is True
    assert done["todo"]["completed_at"] is not None
    reopened = client.patch(f"/api/planner/todos/{tid}", json={"done": False}).json()
    assert reopened["todo"]["done"] is False
    assert reopened["todo"]["completed_at"] is None


def test_update_title_propagates_to_event(client, session_local):
    created = client.post(
        "/api/planner/todos", json={"title": "old", "start_at": DUE_ISO}
    ).json()
    tid = created["todo"]["id"]
    resp = client.patch(f"/api/planner/todos/{tid}", json={"title": "new"})
    assert resp.json()["event"]["title"] == "new"


def test_update_not_found(client):
    assert (
        client.patch("/api/planner/todos/99999", json={"title": "x"}).status_code == 404
    )
