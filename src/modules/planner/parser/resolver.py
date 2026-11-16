"""Deterministic date/reminder resolution (the hybrid's non-LLM half).

Uses ``dateparser`` to turn the SLM's free-text ``date_expr`` / ``reminder_expr``
into concrete values, anchored to the supplied ``now`` in Asia/Bangkok, preferring
future dates. Resolved datetimes are returned as UTC instants (store-UTC, FR-4/
NFR-9). This module is pure-Python and MUST run off the event loop (callers use
``asyncio.to_thread``) because dateparser can be non-trivial (NFR-6).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import dateparser

from core.config import settings


@dataclass(slots=True)
class ResolvedDate:
    start_at: datetime | None  # UTC
    all_day: bool
    date_only: bool  # a date resolved but no explicit time (OQ-4 → all_day)


def _resolve_tz() -> ZoneInfo:
    return ZoneInfo(settings.PLANNER_RESOLVE_TZ)


def _dateparser_settings(now: datetime) -> dict:
    return {
        "TIMEZONE": settings.PLANNER_RESOLVE_TZ,
        "TO_TIMEZONE": "UTC",
        "RETURN_AS_TIMEZONE_AWARE": True,
        "PREFER_DATES_FROM": "future",
        "RELATIVE_BASE": now.replace(tzinfo=None),
    }


def _has_explicit_time(expr: str) -> bool:
    """Heuristic: does the phrase mention a clock time?

    Used to decide the OQ-4 all-day assumption (date-only input → all_day). If a
    phrase carries no time words, we treat it as date-only.
    """
    lowered = expr.lower()
    time_markers = (
        "โมง",
        "นาฬิกา",
        "ทุ่ม",
        "บ่าย",
        "เช้า",
        "เย็น",
        "เที่ยง",
        ":",
        "am",
        "pm",
        "a.m",
        "p.m",
        "o'clock",
        "noon",
        "midnight",
        "morning",
        "afternoon",
        "evening",
        "night",
    )
    if any(marker in lowered for marker in time_markers):
        return True
    # bare hour like "at 9" / "in 2 hours" also implies a time context
    return any(ch.isdigit() for ch in lowered) and any(
        kw in lowered for kw in ("at ", "hour", "ชั่วโมง")
    )


def resolve_date(date_expr: str | None, *, now: datetime) -> ResolvedDate:
    """Resolve a rough date phrase to a UTC ``start_at`` (or unresolved).

    ``now`` must be Asia/Bangkok-aware. Returns ``start_at=None`` when the phrase
    is absent or cannot be resolved (FR-9a — never fabricate a time).
    """
    if not date_expr or not date_expr.strip():
        return ResolvedDate(start_at=None, all_day=False, date_only=False)

    parsed = dateparser.parse(
        date_expr,
        languages=["th", "en"],
        settings=_dateparser_settings(now),
    )
    if parsed is None:
        return ResolvedDate(start_at=None, all_day=False, date_only=False)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)

    date_only = not _has_explicit_time(date_expr)
    if date_only:
        # OQ-4 default: date-only input → an all-day task. Anchor start to the
        # start of that local day, expressed in UTC.
        local = parsed.astimezone(_resolve_tz())
        local_midnight = datetime.combine(
            local.date(), time(0, 0), tzinfo=_resolve_tz()
        )
        parsed = local_midnight.astimezone(UTC)

    return ResolvedDate(start_at=parsed, all_day=date_only, date_only=date_only)


def resolve_lead_time(reminder_expr: str | None, *, now: datetime) -> timedelta | None:
    """Resolve a reminder phrase into a lead-time delta (FR-27).

    Returns ``None`` when the phrase is absent or unresolvable; the caller then
    applies the configured default (FR-24/25). A phrase like "2 hours before" is
    resolved by parsing "2 hours" relative to ``now`` and taking the magnitude of
    the offset.
    """
    if not reminder_expr or not reminder_expr.strip():
        return None

    parsed = dateparser.parse(
        reminder_expr,
        languages=["th", "en"],
        settings={
            "TIMEZONE": settings.PLANNER_RESOLVE_TZ,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": now.replace(tzinfo=None),
        },
    )
    if parsed is None:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    delta = abs(parsed - now)
    if delta <= timedelta(0):
        return None
    return delta
