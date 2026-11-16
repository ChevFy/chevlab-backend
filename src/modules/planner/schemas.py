"""Pydantic v2 request/response schemas for the planner feature.

Wire convention (stage 04 §0/§1.4):
- All datetimes are UTC ISO-8601. Naive datetimes in request bodies are rejected
  (422); offset-aware inputs are normalized to UTC.
- ``lead_time`` is an ISO-8601 duration on the wire (e.g. ``"P1D"``, ``"PT2H"``),
  mapping to a Python ``timedelta`` / Postgres ``INTERVAL``.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _to_utc(value: datetime | None) -> datetime | None:
    """Reject naive datetimes; normalize aware ones to UTC (NFR-9/10)."""
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("datetime must include a timezone offset")
    return value.astimezone(UTC)


# ---------- Response resources ----------


class TodoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    source_text: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    all_day: bool
    done: bool
    completed_at: datetime | None = None
    event_id: int | None = None
    created_at: datetime
    updated_at: datetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    start_at: datetime
    end_at: datetime | None = None
    all_day: bool
    # Derived source todo (from todos.event_id). The service passes this
    # explicitly via EventOut.model_validate(event, context=...) or by setting
    # it on the ORM instance, since Event carries no back-FK column.
    todo_id: int | None = None
    created_at: datetime
    updated_at: datetime


class ReminderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    todo_id: int
    event_id: int | None = None
    lead_time: timedelta
    lead_source: Literal["default", "parsed", "manual"]
    fire_at: datetime
    status: Literal["pending", "sending", "sent", "failed", "expired", "acknowledged"]
    sent_at: datetime | None = None
    acknowledged_at: datetime | None = None


# ---------- Parse-support value objects ----------


class Assumption(BaseModel):
    """A default the system filled, surfaced to the operator (FR-9)."""

    field: str
    value: str
    reason: str


class ReminderIn(BaseModel):
    """A reminder lead-time on create/update (structured path, FR-28)."""

    lead_time: timedelta

    @field_validator("lead_time")
    @classmethod
    def _positive(cls, value: timedelta) -> timedelta:
        if value <= timedelta(0):
            raise ValueError("lead_time must be positive")
        return value


# ---------- To-do request bodies ----------


class TodoCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    start_at: datetime | None = None
    end_at: datetime | None = None
    all_day: bool = False
    reminders: list[ReminderIn] | None = None

    _normalize_start = field_validator("start_at")(_to_utc)
    _normalize_end = field_validator("end_at")(_to_utc)

    @model_validator(mode="after")
    def _check(self) -> "TodoCreate":
        if (
            self.start_at is not None
            and self.end_at is not None
            and self.end_at < self.start_at
        ):
            raise ValueError("end_at must be >= start_at")
        if self.reminders and self.start_at is None:
            raise ValueError("reminders require a start_at (floating tasks cannot)")
        return self


class TodoParse(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class TodoUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    start_at: datetime | None = None
    end_at: datetime | None = None
    all_day: bool | None = None
    done: bool | None = None
    reminders: list[ReminderIn] | None = None

    _normalize_start = field_validator("start_at")(_to_utc)
    _normalize_end = field_validator("end_at")(_to_utc)

    @model_validator(mode="after")
    def _check(self) -> "TodoUpdate":
        if (
            self.start_at is not None
            and self.end_at is not None
            and self.end_at < self.start_at
        ):
            raise ValueError("end_at must be >= start_at")
        return self


# ---------- Responses that compose resources ----------


class TodoCreated(BaseModel):
    todo: TodoOut
    event: EventOut | None = None
    reminders: list[ReminderOut] = []


class TodoList(BaseModel):
    items: list[TodoOut]
    total: int
    limit: int
    offset: int


class EventList(BaseModel):
    items: list[EventOut]


# ---------- Parse results & errors ----------


class ParseResult(BaseModel):
    todo: TodoOut
    event: EventOut | None = None
    reminders: list[ReminderOut] = []
    assumptions: list[Assumption] = []


class ParseCandidate(BaseModel):
    title: str | None = None
    date_expr: str | None = None
    reminder_expr: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    all_day: bool | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    assumptions: list[Assumption] | None = None
    candidate: ParseCandidate | None = None


# ---------- Reminder in-app feed ----------


class ReminderFeedItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    todo_id: int
    title: str
    fire_at: datetime
    delivered_at: datetime | None = None
    start_at: datetime | None = None
    is_late: bool = False


class ReminderFeed(BaseModel):
    items: list[ReminderFeedItem]
