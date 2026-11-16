"""GET /events range query (AC-6, FR-18/20) — overlap semantics, null-end point.

Events are created only through dated todos (no event-write endpoint). An event
with null ``end_at`` is treated as a point at ``start_at``.
"""

from datetime import UTC, datetime, timedelta

import pytest


def _mk_dated(client, title, start, end=None):
    body = {"title": title, "start_at": start.isoformat()}
    if end is not None:
        body["end_at"] = end.isoformat()
    return client.post("/api/planner/todos", json=body).json()


def _events(client, frm, to):
    return client.get(
        "/api/planner/events", params={"from": frm.isoformat(), "to": to.isoformat()}
    )


BASE = datetime(2027, 1, 10, 12, 0, tzinfo=UTC)


def test_point_event_inside_window(client):
    _mk_dated(client, "point", BASE)
    resp = _events(client, BASE - timedelta(hours=1), BASE + timedelta(hours=1))
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1


def test_spanning_event_overlapping_window_start(client):
    """FR-20: an event that starts BEFORE the window but overlaps it is returned."""
    _mk_dated(client, "span", BASE, end=BASE + timedelta(hours=4))  # 12:00–16:00
    # window 14:00–18:00 starts inside the event span
    resp = _events(client, BASE + timedelta(hours=2), BASE + timedelta(hours=6))
    assert len(resp.json()["items"]) == 1


def test_event_entirely_before_window_excluded(client):
    _mk_dated(client, "past", BASE, end=BASE + timedelta(hours=1))
    resp = _events(client, BASE + timedelta(days=1), BASE + timedelta(days=2))
    assert resp.json()["items"] == []


def test_events_ordered_by_start(client):
    _mk_dated(client, "later", BASE + timedelta(hours=5))
    _mk_dated(client, "earlier", BASE)
    resp = _events(client, BASE - timedelta(days=1), BASE + timedelta(days=1))
    titles = [e["title"] for e in resp.json()["items"]]
    assert titles == ["earlier", "later"]


def test_event_carries_source_todo_id(client):
    created = _mk_dated(client, "linked", BASE)
    resp = _events(client, BASE - timedelta(days=1), BASE + timedelta(days=1))
    item = resp.json()["items"][0]
    assert item["todo_id"] == created["todo"]["id"]


def test_events_requires_from_and_to(client):
    assert client.get("/api/planner/events").status_code == 422
    assert (
        client.get("/api/planner/events", params={"from": BASE.isoformat()}).status_code
        == 422
    )


def test_events_rejects_to_before_from(client):
    resp = client.get(
        "/api/planner/events",
        params={"from": BASE.isoformat(), "to": (BASE - timedelta(days=1)).isoformat()},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "'to' must be >= 'from'"


def test_events_rejects_naive_bounds(client):
    """NFR-9: naive from/to rejected (AwareDatetime)."""
    naive = BASE.replace(tzinfo=None)
    resp = client.get(
        "/api/planner/events",
        params={
            "from": naive.isoformat(),
            "to": (naive + timedelta(days=1)).isoformat(),
        },
    )
    assert resp.status_code == 422


def test_empty_window_returns_empty(client):
    """A valid but empty window returns an empty list, not an error."""
    resp = _events(client, BASE, BASE + timedelta(seconds=1))
    assert resp.status_code == 200
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_large_window_returns_all(client, session_local):
    for i in range(5):
        _mk_dated(client, f"e{i}", BASE + timedelta(days=i))
    resp = _events(client, BASE - timedelta(days=365), BASE + timedelta(days=365))
    assert len(resp.json()["items"]) == 5
