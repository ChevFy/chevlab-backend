"""Pydantic v2 schema unit tests (wire contract §1.4, §2)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from modules.planner import schemas


def test_todocreate_rejects_naive_datetime():
    with pytest.raises(ValidationError):
        schemas.TodoCreate(title="x", start_at=datetime(2026, 12, 1, 3, 0))


def test_todocreate_normalizes_offset_to_utc():
    # +07:00 Bangkok 10:00 == 03:00Z
    bkk = timezone(timedelta(hours=7))
    body = schemas.TodoCreate(
        title="x", start_at=datetime(2026, 12, 1, 10, 0, tzinfo=bkk)
    )
    assert body.start_at == datetime(2026, 12, 1, 3, 0, tzinfo=UTC)


def test_todocreate_end_before_start_rejected():
    with pytest.raises(ValidationError):
        schemas.TodoCreate(
            title="x",
            start_at=datetime(2026, 12, 1, 3, 0, tzinfo=UTC),
            end_at=datetime(2026, 12, 1, 2, 0, tzinfo=UTC),
        )


def test_todocreate_reminders_require_start():
    with pytest.raises(ValidationError):
        schemas.TodoCreate(
            title="x", reminders=[schemas.ReminderIn(lead_time=timedelta(hours=1))]
        )


def test_reminderin_rejects_nonpositive():
    with pytest.raises(ValidationError):
        schemas.ReminderIn(lead_time=timedelta(0))
    with pytest.raises(ValidationError):
        schemas.ReminderIn(lead_time=timedelta(hours=-1))


def test_reminderin_parses_iso_duration():
    r = schemas.ReminderIn.model_validate({"lead_time": "PT2H"})
    assert r.lead_time == timedelta(hours=2)


def test_reminderout_serializes_lead_time_as_iso_duration():
    out = schemas.ReminderOut(
        id=1,
        todo_id=1,
        event_id=1,
        lead_time=timedelta(days=1),
        lead_source="default",
        fire_at=datetime(2026, 12, 1, 3, 0, tzinfo=UTC),
        status="pending",
    )
    dumped = out.model_dump(mode="json")
    assert dumped["lead_time"] == "P1D"


def test_error_detail_shape_with_candidate():
    detail = schemas.ErrorDetail(
        code="parse_ambiguous",
        message="could not resolve",
        candidate=schemas.ParseCandidate(title="meeting", date_expr="next week"),
    )
    dumped = detail.model_dump(mode="json")
    assert dumped["code"] == "parse_ambiguous"
    assert dumped["candidate"]["title"] == "meeting"
    assert dumped["candidate"]["start_at"] is None


def test_todoparse_max_length():
    schemas.TodoParse(text="a" * 500)  # ok
    with pytest.raises(ValidationError):
        schemas.TodoParse(text="a" * 501)
    with pytest.raises(ValidationError):
        schemas.TodoParse(text="")


def test_todoupdate_partial_distinguishes_unset_from_null():
    # start_at omitted → not in the dump
    only_title = schemas.TodoUpdate(title="new").model_dump(exclude_unset=True)
    assert only_title == {"title": "new"}
    # start_at explicitly null → present as None (clears → floating)
    cleared = schemas.TodoUpdate(start_at=None).model_dump(exclude_unset=True)
    assert "start_at" in cleared and cleared["start_at"] is None
