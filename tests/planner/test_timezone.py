"""Timezone correctness (AC-8, FR-4, NFR-9/10).

Bangkok is UTC+7 (no DST). The operator's relative phrase resolves against
Asia/Bangkok local time, is stored UTC, and emitted UTC on the wire. These are
unit tests against the real ``resolver`` plus an end-to-end parse check.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from modules.planner.parser.base import RawExtraction
from modules.planner.parser.resolver import resolve_date, resolve_lead_time

BKK = ZoneInfo("Asia/Bangkok")


def test_tomorrow_9am_resolves_to_0200z():
    """AC-8: 'tomorrow 9am' in Bangkok == 02:00 UTC next day (not 09:00 UTC)."""
    now = datetime(2026, 11, 15, 12, 0, tzinfo=BKK)
    resolved = resolve_date("tomorrow 09:00", now=now)
    assert resolved.start_at is not None
    assert resolved.start_at.tzinfo is not None
    assert resolved.start_at.astimezone(UTC) == datetime(2026, 11, 16, 2, 0, tzinfo=UTC)
    assert resolved.all_day is False


def test_tomorrow_3pm_resolves_to_0800z():
    """AC-10 anchor: 15:00 Bangkok == 08:00 UTC."""
    now = datetime(2026, 11, 15, 10, 0, tzinfo=BKK)
    resolved = resolve_date("tomorrow 15:00", now=now)
    assert resolved.start_at.astimezone(UTC) == datetime(2026, 11, 16, 8, 0, tzinfo=UTC)


def test_date_only_anchors_to_local_midnight_utc():
    """A date with no time → local-midnight Bangkok, expressed UTC (17:00Z prior day)."""
    now = datetime(2026, 11, 15, 12, 0, tzinfo=BKK)
    resolved = resolve_date("2026-12-01", now=now)
    assert resolved.date_only is True
    assert resolved.all_day is True
    # 2026-12-01 00:00 Bangkok == 2026-11-30 17:00 UTC
    assert resolved.start_at.astimezone(UTC) == datetime(
        2026, 11, 30, 17, 0, tzinfo=UTC
    )


def test_unresolvable_phrase_returns_none():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=BKK)
    assert resolve_date("zzz nonsense qweqwe", now=now).start_at is None


def test_lead_time_two_hours():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=BKK)
    delta = resolve_lead_time("2 hours before", now=now)
    assert delta == timedelta(hours=2)


def test_lead_time_absent_returns_none():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=BKK)
    assert resolve_lead_time(None, now=now) is None
    assert resolve_lead_time("   ", now=now) is None


# --- end-to-end parse tz (stubbed parser, real resolver) -------------------


def test_parse_emits_utc_on_wire(client, app):
    from modules.planner.deps import get_parser

    class _P:
        async def extract(self, text, *, now):
            # Anchor relative expression to a fixed Bangkok 'now' inside the stub
            # is not possible (service supplies now); use an absolute time instead.
            return RawExtraction(title="m", date_expr="2026-12-01 15:00")

        async def available(self):
            return True

    app.dependency_overrides[get_parser] = lambda: _P()
    try:
        resp = client.post("/api/planner/todos/parse", json={"text": "m dec 1 3pm"})
        assert resp.status_code == 201, resp.text
        start = resp.json()["todo"]["start_at"]
        # 15:00 Bangkok on 2026-12-01 == 08:00 UTC.
        assert datetime.fromisoformat(start).astimezone(UTC) == datetime(
            2026, 12, 1, 8, 0, tzinfo=UTC
        )
    finally:
        app.dependency_overrides.pop(get_parser, None)


def test_todos_date_filter_translates_bangkok_day(client):
    """AC-5/NFR-10: ?date is a Bangkok calendar day → UTC half-open window.

    A todo at 2026-12-01 18:30 UTC is 2026-12-02 01:30 Bangkok, so it matches
    the Bangkok day 2026-12-02, NOT 2026-12-01.
    """
    # 2026-12-01T18:30Z == 2026-12-02 01:30 Bangkok
    start = datetime(2026, 12, 1, 18, 30, tzinfo=UTC)
    client.post(
        "/api/planner/todos",
        json={"title": "cross-midnight", "start_at": start.isoformat()},
    )
    # Bangkok day 2026-12-02 should contain it.
    hit = client.get("/api/planner/todos?date=2026-12-02").json()
    assert hit["total"] == 1
    # Bangkok day 2026-12-01 should NOT.
    miss = client.get("/api/planner/todos?date=2026-12-01").json()
    assert miss["total"] == 0


def test_events_overlap_window_translates_utc(client):
    """AC-6/FR-20: overlap predicate honours the exact UTC window given."""
    due = datetime(2026, 12, 10, 3, 0, tzinfo=UTC)
    client.post("/api/planner/todos", json={"title": "ev", "start_at": due.isoformat()})
    # Window straddling the event start.
    inside = client.get(
        "/api/planner/events",
        params={
            "from": (due - timedelta(hours=1)).isoformat(),
            "to": (due + timedelta(hours=1)).isoformat(),
        },
    ).json()
    assert len(inside["items"]) == 1
    # Window entirely before the event → excluded.
    before = client.get(
        "/api/planner/events",
        params={
            "from": (due - timedelta(days=2)).isoformat(),
            "to": (due - timedelta(days=1)).isoformat(),
        },
    ).json()
    assert len(before["items"]) == 0
