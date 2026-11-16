"""Structured create/list/get/update/delete integration (AC-4/5, FR-11..17).

Also covers the default-reminder behaviour (AC-9/11/12 scheduling side) and the
todo↔event link lifecycle in ``update_todo`` (FR-16/17/29).
"""

from datetime import UTC, datetime, timedelta

import pytest

FUTURE = (datetime.now(UTC) + timedelta(days=10)).replace(microsecond=0)
FUTURE_ISO = FUTURE.isoformat()


# --- POST /todos — structured create (AC-4) --------------------------------


def test_create_floating_todo_no_event_no_reminder(client):
    """AC-2/AC-12: floating todo → no event, no reminder."""
    resp = client.post("/api/planner/todos", json={"title": "buy milk"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["todo"]["start_at"] is None
    assert body["todo"]["event_id"] is None
    assert body["event"] is None
    assert body["reminders"] == []


def test_create_dated_todo_default_reminder_one_day(client):
    """AC-9: dated todo, no reminder stated → default 1-day reminder scheduled."""
    resp = client.post(
        "/api/planner/todos", json={"title": "Pay rent", "start_at": FUTURE_ISO}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["event"] is not None
    assert body["event"]["todo_id"] == body["todo"]["id"]
    assert body["todo"]["event_id"] == body["event"]["id"]
    assert len(body["reminders"]) == 1
    rem = body["reminders"][0]
    assert rem["lead_source"] == "default"
    assert rem["lead_time"] == "P1D"
    # fire_at == due - 1 day
    fire = datetime.fromisoformat(rem["fire_at"])
    assert fire == FUTURE - timedelta(days=1)
    assert rem["status"] == "pending"


def test_create_dated_todo_explicit_reminder_manual(client):
    resp = client.post(
        "/api/planner/todos",
        json={
            "title": "Call",
            "start_at": FUTURE_ISO,
            "reminders": [{"lead_time": "PT2H"}],
        },
    )
    assert resp.status_code == 201, resp.text
    rems = resp.json()["reminders"]
    assert len(rems) == 1
    assert rems[0]["lead_source"] == "manual"
    assert rems[0]["lead_time"] == "PT2H"


def test_create_dated_todo_empty_reminders_clears(client):
    """Explicit [] means NO reminders (FR-28 clear semantics)."""
    resp = client.post(
        "/api/planner/todos",
        json={"title": "silent", "start_at": FUTURE_ISO, "reminders": []},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["reminders"] == []


# --- validation errors (422) ----------------------------------------------


def test_create_rejects_naive_datetime(client):
    """NFR-9: naive datetime in a body is rejected (422)."""
    naive = FUTURE.replace(tzinfo=None).isoformat()
    resp = client.post("/api/planner/todos", json={"title": "x", "start_at": naive})
    assert resp.status_code == 422, resp.text


def test_create_rejects_floating_with_reminders(client):
    """FR-26: reminders require a start_at."""
    resp = client.post(
        "/api/planner/todos",
        json={"title": "x", "reminders": [{"lead_time": "PT1H"}]},
    )
    assert resp.status_code == 422, resp.text


def test_create_rejects_end_before_start(client):
    end = (FUTURE - timedelta(hours=1)).isoformat()
    resp = client.post(
        "/api/planner/todos",
        json={"title": "x", "start_at": FUTURE_ISO, "end_at": end},
    )
    assert resp.status_code == 422, resp.text


def test_create_rejects_blank_title(client):
    resp = client.post("/api/planner/todos", json={"title": ""})
    assert resp.status_code == 422


def test_create_rejects_nonpositive_lead_time(client):
    resp = client.post(
        "/api/planner/todos",
        json={
            "title": "x",
            "start_at": FUTURE_ISO,
            "reminders": [{"lead_time": "PT0S"}],
        },
    )
    assert resp.status_code == 422


# --- GET /todos list + filters (AC-5) --------------------------------------


@pytest.fixture
def seeded(client):
    """Three todos: one floating, two dated on different local days."""
    # done floating
    t1 = client.post("/api/planner/todos", json={"title": "floating-done"}).json()
    client.patch(f"/api/planner/todos/{t1['todo']['id']}", json={"done": True})
    # dated day A (far future)
    day_a = (datetime.now(UTC) + timedelta(days=20)).replace(
        hour=3, minute=0, second=0, microsecond=0
    )
    client.post(
        "/api/planner/todos", json={"title": "dated-A", "start_at": day_a.isoformat()}
    )
    # dated day B
    day_b = (datetime.now(UTC) + timedelta(days=40)).replace(
        hour=3, minute=0, second=0, microsecond=0
    )
    client.post(
        "/api/planner/todos", json={"title": "dated-B", "start_at": day_b.isoformat()}
    )
    return {"day_a": day_a, "day_b": day_b}


def test_list_all(client, seeded):
    body = client.get("/api/planner/todos").json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


def test_list_filter_done(client, seeded):
    done = client.get("/api/planner/todos?done=true").json()
    assert done["total"] == 1
    assert done["items"][0]["title"] == "floating-done"
    not_done = client.get("/api/planner/todos?done=false").json()
    assert not_done["total"] == 2


def test_list_filter_by_date_bangkok_day(client, seeded):
    """date filter is interpreted as an Asia/Bangkok local day (NFR-10)."""
    # day_a UTC 03:00 → Bangkok 10:00 same calendar day.
    day_a = seeded["day_a"]
    resp = client.get(f"/api/planner/todos?date={day_a.date().isoformat()}")
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "dated-A"
    # Floating todos are excluded when date is set.
    assert all(i["start_at"] is not None for i in body["items"])


def test_list_pagination(client, seeded):
    page = client.get("/api/planner/todos?limit=2&offset=0").json()
    assert page["limit"] == 2 and page["offset"] == 0
    assert len(page["items"]) == 2
    assert page["total"] == 3


def test_list_limit_bounds_rejected(client):
    assert client.get("/api/planner/todos?limit=0").status_code == 422
    assert client.get("/api/planner/todos?limit=501").status_code == 422
    assert client.get("/api/planner/todos?offset=-1").status_code == 422


# --- GET /todos/{id} -------------------------------------------------------


def test_get_one_found(client):
    tid = client.post("/api/planner/todos", json={"title": "x"}).json()["todo"]["id"]
    resp = client.get(f"/api/planner/todos/{tid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == tid


def test_get_one_not_found(client):
    resp = client.get("/api/planner/todos/99999")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Todo not found"


# --- DELETE (FR-15/16 cascade) ---------------------------------------------


def test_delete_cascades_event_and_reminders(client):
    created = client.post(
        "/api/planner/todos", json={"title": "del", "start_at": FUTURE_ISO}
    ).json()
    tid = created["todo"]["id"]
    resp = client.delete(f"/api/planner/todos/{tid}")
    assert resp.status_code == 204
    assert resp.content == b""
    # gone
    assert client.get(f"/api/planner/todos/{tid}").status_code == 404
    # its event no longer surfaces in a wide range query
    events = client.get(
        "/api/planner/events",
        params={
            "from": (FUTURE - timedelta(days=5)).isoformat(),
            "to": (FUTURE + timedelta(days=5)).isoformat(),
        },
    ).json()["items"]
    assert all(e["id"] != created["event"]["id"] for e in events)


def test_delete_not_found(client):
    assert client.delete("/api/planner/todos/99999").status_code == 404
