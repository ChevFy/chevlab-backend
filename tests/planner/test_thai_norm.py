"""Thai NL date/clock normaliser (``thai_norm.normalize_thai``) + resolver wiring.

``dateparser`` mishandles many Thai relative-day phrases and has no support for
Thai analog-clock vocabulary. ``normalize_thai`` rewrites those into ISO dates /
24h ``HH:MM`` that dateparser resolves correctly. These tests assert the RESOLVED
local time/date (via the real ``resolve_date`` / ``resolve_lead_time``), anchored
to a fixed ``now`` matching the original empirical probe:

    now = Tue 2026-11-17 09:00 Asia/Bangkok

They also prove the already-working cases (พรุ่งนี้, 07:00, 7am, English) are not
broken, and that date-only phrases stay ``all_day`` while clock phrases do not.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from modules.planner.parser.resolver import resolve_date, resolve_lead_time
from modules.planner.parser.thai_norm import normalize_thai

BKK = ZoneInfo("Asia/Bangkok")
NOW = datetime(2026, 11, 17, 9, 0, tzinfo=BKK)  # Tuesday
# Afternoon anchor for past-today cases (a morning clock on 'today' is in the past).
NOW_PM = datetime(2026, 11, 17, 14, 0, tzinfo=BKK)  # Tuesday 14:00


def _local(expr: str, *, now: datetime = NOW):
    """Resolve ``expr`` and return its Asia/Bangkok-local datetime (or None)."""
    r = resolve_date(expr, now=now)
    if r.start_at is None:
        return None
    return r.start_at.astimezone(BKK)


def _local_time(expr: str) -> time:
    dt = _local(expr)
    assert dt is not None, f"{expr!r} did not resolve"
    return dt.timetz().replace(tzinfo=None)


def _local_date(expr: str):
    dt = _local(expr)
    assert dt is not None, f"{expr!r} did not resolve"
    return dt.date()


# ---------------------------------------------------------------------------
# Clock forms → explicit local time (assert resolved HH:MM)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr,hh,mm",
    [
        ("ตีห้า", 5, 0),  # ตี N → 0N:00
        ("ตี 3", 3, 0),
        ("7 โมงเช้า", 7, 0),  # N โมงเช้า (6–11)
        ("เวลา7 โมงเช้า", 7, 0),  # no-space case
        ("บ่ายโมง", 13, 0),  # บ่ายโมง = 13:00
        ("บ่าย 3 โมง", 15, 0),  # บ่าย N โมง = (12+N):00
        ("4 โมงเย็น", 16, 0),  # N โมงเย็น (4–6) = (12+N):00
        ("6 โมงเย็น", 18, 0),
        ("สองทุ่ม", 20, 0),  # N ทุ่ม = (18+N):00
        ("1 ทุ่ม", 19, 0),
        ("5 นาฬิกา", 5, 0),  # N นาฬิกา = N:00 literal
        ("19 นาฬิกา", 19, 0),
        ("เที่ยง", 12, 0),  # noon
        ("เที่ยงวัน", 12, 0),
        ("เที่ยงคืน", 0, 0),  # midnight
        ("7 โมงครึ่ง", 7, 30),  # ครึ่ง → :30
        ("บ่าย 2 โมงครึ่ง", 14, 30),
    ],
)
def test_clock_forms_resolve_to_expected_time(expr, hh, mm):
    assert _local_time(expr) == time(hh, mm)


def test_thai_numeral_word_counters():
    """Thai number WORDS work as clock counters, not just Arabic digits."""
    assert _local_time("สองทุ่ม") == time(20, 0)  # สอง = 2 → 18+2
    assert _local_time("ตีห้า") == time(5, 0)  # ห้า = 5
    assert _local_time("เก้าโมงเช้า") == time(9, 0)  # เก้า = 9


def test_clock_phrase_is_not_all_day():
    """A clock phrase carries explicit time → not all_day (FR-9a time respected)."""
    r = resolve_date("ตีห้า", now=NOW)
    assert r.start_at is not None
    assert r.all_day is False
    assert r.date_only is False


# ---------------------------------------------------------------------------
# Relative-day gaps / bugs → correct local date (date-only → all_day)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr,y,m,d",
    [
        ("มะรืน", 2026, 11, 19),  # +2 (dateparser → None)
        ("มะรืนนี้", 2026, 11, 19),
        ("วันมะรืน", 2026, 11, 19),
        ("เมื่อวานนี้", 2026, 11, 16),  # -1 (dateparser bug → +1)
        ("เมื่อวานซืน", 2026, 11, 15),  # -2 (dateparser bug → +2)
        ("2 วันก่อน", 2026, 11, 15),  # -N
        ("3 วันที่แล้ว", 2026, 11, 14),
        ("อาทิตย์หน้า", 2026, 11, 24),  # +7
        ("สัปดาห์ที่แล้ว", 2026, 11, 10),  # -7
        ("วันอังคารหน้า", 2026, 11, 24),  # next Tue strictly after now
        ("วันจันทร์หน้า", 2026, 11, 23),  # next Mon
        ("สิ้นเดือน", 2026, 11, 30),  # last day of month
        ("ต้นเดือนหน้า", 2026, 12, 1),  # first day of next month
    ],
)
def test_relative_day_forms_resolve_to_expected_date(expr, y, m, d):
    assert _local_date(expr) == datetime(y, m, d, tzinfo=BKK).date()


def test_date_only_phrase_is_all_day():
    """A day-only phrase (มะรืน) → all_day, no fabricated time (FR-9a)."""
    r = resolve_date("มะรืน", now=NOW)
    assert r.start_at is not None
    assert r.all_day is True
    assert r.date_only is True
    # Anchored to local midnight of the computed day.
    assert r.start_at.astimezone(BKK).timetz().replace(tzinfo=None) == time(0, 0)


# ---------------------------------------------------------------------------
# Mixed day + clock → single resolved datetime
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr,y,m,d,hh,mm",
    [
        ("มะรืน 2 ทุ่ม", 2026, 11, 19, 20, 0),
        ("พรุ่งนี้ 7 โมงเช้า", 2026, 11, 18, 7, 0),
        ("มะรืนนี้ บ่าย 2 โมงครึ่ง", 2026, 11, 19, 14, 30),
        ("เมื่อวานนี้ เที่ยง", 2026, 11, 16, 12, 0),
    ],
)
def test_mixed_day_and_clock(expr, y, m, d, hh, mm):
    dt = _local(expr)
    assert dt is not None, f"{expr!r} did not resolve"
    assert dt.date() == datetime(y, m, d, tzinfo=BKK).date()
    assert dt.timetz().replace(tzinfo=None) == time(hh, mm)
    assert resolve_date(expr, now=NOW).all_day is False


# ---------------------------------------------------------------------------
# No breakage: phrases that already worked (dateparser untouched)
# ---------------------------------------------------------------------------


def test_regression_existing_working_phrases():
    assert _local_date("พรุ่งนี้") == datetime(2026, 11, 18, tzinfo=BKK).date()
    assert _local_time("07:00") == time(7, 0)
    assert _local_time("7am") == time(7, 0)
    # English mixed day+clock still fine.
    dt = _local("tomorrow 9am")
    assert dt.date() == datetime(2026, 11, 18, tzinfo=BKK).date()
    assert dt.timetz().replace(tzinfo=None) == time(9, 0)


def test_pass_through_leaves_unknown_untouched():
    """Normaliser must not rewrite input it doesn't recognise (idempotent)."""
    assert normalize_thai("07:00", now=NOW) == "07:00"
    assert normalize_thai("tomorrow 9am", now=NOW) == "tomorrow 9am"
    assert normalize_thai("zzz nonsense", now=NOW) == "zzz nonsense"
    # Already-ISO input is left alone.
    assert normalize_thai("2026-12-01 15:00", now=NOW) == "2026-12-01 15:00"


def test_unresolvable_still_none():
    assert resolve_date("zzz nonsense qweqwe", now=NOW).start_at is None


# ---------------------------------------------------------------------------
# Lead-time path also runs the normaliser (no regression)
# ---------------------------------------------------------------------------


def test_lead_time_unaffected():
    from datetime import timedelta

    assert resolve_lead_time("2 hours before", now=NOW) == timedelta(hours=2)
    assert resolve_lead_time(None, now=NOW) is None
