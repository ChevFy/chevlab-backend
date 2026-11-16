"""POST /todos/parse — the headline NL-capture endpoint (FR-1..10, AC-1/2/3/10).

The ``Parser`` is always stubbed (Ollama never runs). The deterministic resolver
(``dateparser``) runs for real so timezone resolution is genuinely exercised.

Outcomes under test:
  A — success (persisted) → 201, with event/reminders/assumptions
  B — ambiguous (date_expr the resolver can't handle) → 422 + ParseCandidate,
      NOTHING persisted
  C — parser unavailable / timeout / failed → 503, NOTHING persisted
"""

import pytest
from sqlalchemy import func, select

from modules.planner.deps import get_parser
from modules.planner.models import Todo
from modules.planner.parser.base import (
    ParseFailed,
    ParserUnavailable,
    ParseTimeout,
    RawExtraction,
)

from .stubs import StubParser


@pytest.fixture
def parse_with(app):
    """Install a StubParser and return (post_fn, stub)."""
    stubs = {}

    def _install(**kwargs):
        stub = StubParser(**kwargs)
        app.dependency_overrides[get_parser] = lambda: stub
        stubs["stub"] = stub
        return stub

    yield _install
    app.dependency_overrides.pop(get_parser, None)


async def _todo_count(session_local):
    async with session_local() as s:
        return await s.scalar(select(func.count()).select_from(Todo))


# --- Outcome A: success ----------------------------------------------------


def test_parse_success_dated_with_parsed_reminder(client, parse_with):
    """AC-10: 'tomorrow 15:00, remind 2h before' → dated todo + 2h reminder."""
    stub = parse_with(
        extraction=RawExtraction(
            title="meeting",
            date_expr="tomorrow 15:00",
            reminder_expr="2 hours before",
        )
    )
    resp = client.post(
        "/api/planner/todos/parse",
        json={"text": "meeting tomorrow 15:00 remind 2h before"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["todo"]["title"] == "meeting"
    assert body["event"] is not None
    assert len(body["reminders"]) == 1
    assert body["reminders"][0]["lead_source"] == "parsed"
    assert body["reminders"][0]["lead_time"] == "PT2H"
    assert stub.calls == 1  # NFR-3: exactly one model call per parse


def test_parse_success_floating_has_assumption(client, parse_with):
    """AC-2: no date → floating task, event null, assumption explains it."""
    parse_with(extraction=RawExtraction(title="buy milk", date_expr=None))
    resp = client.post("/api/planner/todos/parse", json={"text": "buy milk"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["event"] is None
    assert body["reminders"] == []
    assert any(a["field"] == "start_at" for a in body["assumptions"])


def test_parse_success_dated_no_reminder_uses_default(client, parse_with):
    """Dated input with no reminder phrase → default lead applied + assumption."""
    parse_with(
        extraction=RawExtraction(
            title="appt", date_expr="tomorrow 09:00", reminder_expr=None
        )
    )
    resp = client.post("/api/planner/todos/parse", json={"text": "appt tomorrow 9am"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body["reminders"]) == 1
    assert body["reminders"][0]["lead_source"] == "default"
    assert any(a["field"] == "lead_time" for a in body["assumptions"])


def test_parse_date_only_is_all_day_with_assumption(client, parse_with):
    """OQ-4: a date but no time → all_day task + assumption row."""
    parse_with(extraction=RawExtraction(title="pay rent", date_expr="next Monday"))
    resp = client.post(
        "/api/planner/todos/parse", json={"text": "pay rent next monday"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["todo"]["all_day"] is True
    assert any(a["field"] == "all_day" for a in body["assumptions"])


# --- Outcome B: ambiguous (422, nothing persisted) -------------------------


def test_parse_ambiguous_returns_422_candidate_nothing_persisted(
    client, parse_with, session_local
):
    """FR-9a: date_expr present but resolver can't resolve → 422 + candidate."""
    parse_with(extraction=RawExtraction(title="meeting", date_expr="asdlkfjqwperu zzz"))
    resp = client.post(
        "/api/planner/todos/parse", json={"text": "meeting asdlkfjqwperu zzz"}
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "parse_ambiguous"
    assert detail["candidate"]["title"] == "meeting"
    assert detail["candidate"]["date_expr"] == "asdlkfjqwperu zzz"
    assert detail["candidate"]["start_at"] is None


@pytest.mark.asyncio
async def test_parse_ambiguous_persists_nothing(client, parse_with, session_local):
    parse_with(extraction=RawExtraction(title="x", date_expr="qwtyuiop zzz nonsense"))
    client.post("/api/planner/todos/parse", json={"text": "x qwtyuiop zzz nonsense"})
    assert await _todo_count(session_local) == 0


# --- Outcome C: parser failure (503, nothing persisted) --------------------


@pytest.mark.parametrize(
    "exc,code",
    [
        (ParserUnavailable("down"), "parser_unavailable"),
        (ParseTimeout("slow"), "parse_timeout"),
        (ParseFailed("garbage"), "parse_failed"),
    ],
)
def test_parse_failure_503_codes(client, parse_with, exc, code):
    parse_with(raises=exc)
    resp = client.post("/api/planner/todos/parse", json={"text": "anything"})
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["code"] == code


@pytest.mark.asyncio
async def test_parse_failure_persists_nothing(client, parse_with, session_local):
    parse_with(raises=ParserUnavailable("down"))
    client.post("/api/planner/todos/parse", json={"text": "anything"})
    assert await _todo_count(session_local) == 0


def test_parse_unexpected_exception_maps_to_503(client, parse_with):
    """A non-ParserError raised by the parser is coerced to 503 (never 500)."""
    parse_with(raises=RuntimeError("boom"))
    resp = client.post("/api/planner/todos/parse", json={"text": "x"})
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "parser_unavailable"


# --- input validation (422) ------------------------------------------------


def test_parse_rejects_oversize_input(client, parse_with):
    parse_with(extraction=RawExtraction(title="x"))
    resp = client.post("/api/planner/todos/parse", json={"text": "a" * 501})
    assert resp.status_code == 422


def test_parse_rejects_empty_input(client, parse_with):
    parse_with(extraction=RawExtraction(title="x"))
    resp = client.post("/api/planner/todos/parse", json={"text": ""})
    assert resp.status_code == 422
