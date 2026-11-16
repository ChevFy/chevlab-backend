"""Deterministic Thai date/clock normalizer (pre-pass for ``dateparser``).

``dateparser`` (languages ``['th','en']``) has broken or missing support for a
number of common Thai relative-day phrases and essentially zero support for
Thai analog-clock vocabulary (``โมง`` / ``ทุ่ม`` / ``ตี`` / ``เที่ยง`` ...). This
module rewrites those Thai forms into shapes ``dateparser`` already resolves
correctly:

* relative-day phrases → an explicit ISO ``YYYY-MM-DD`` computed from ``now``
  (sidestepping dateparser's buggy relative handling entirely), and
* clock phrases → a 24h ``HH:MM`` literal.

``normalize_thai`` is pure, deterministic and side-effect free (it runs under the
resolver's ``asyncio.to_thread`` offload — no event-loop contract is touched).
Anything it does not recognise is left UNTOUCHED so dateparser still gets its
already-working cases (``พรุ่งนี้`` alone, ``อีก 2 ชั่วโมง``, ``07:00``, English).
It is idempotent on already-numeric input.
"""

from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta

__all__ = ["normalize_thai"]

# --- Thai numeral words → int (clock counters) -----------------------------
# "นึง"/"หนึ่ง" = 1 ... "เที่ยง" = 12 handled by its own clock rule.
_THAI_NUM_WORDS: dict[str, int] = {
    "หนึ่ง": 1,
    "นึง": 1,
    "เอ็ด": 1,
    "สอง": 2,
    "สาม": 3,
    "สี่": 4,
    "ห้า": 5,
    "หก": 6,
    "เจ็ด": 7,
    "แปด": 8,
    "เก้า": 9,
    "สิบเอ็ด": 11,
    "สิบ": 10,
}

# A number token: Arabic digits OR one of the Thai number words.
_NUM_WORD_ALT = "|".join(sorted(_THAI_NUM_WORDS, key=len, reverse=True))
_NUM = rf"(?:\d{{1,2}}|{_NUM_WORD_ALT})"


def _num(token: str) -> int:
    token = token.strip()
    if token.isdigit():
        return int(token)
    return _THAI_NUM_WORDS[token]


def _hhmm(hour: int, minute: int = 0) -> str:
    return f"{hour % 24:02d}:{minute:02d}"


# ---------------------------------------------------------------------------
# Clock normalisation
# ---------------------------------------------------------------------------
#
# Each entry is (compiled pattern, handler). Handlers receive the match and
# return an ``HH:MM`` string (or None to decline). Patterns are tried in order;
# the FIRST match wins and is substituted into the string. "ครึ่ง" (half) right
# after a clock phrase contributes the :30 minutes.


def _half_after(expr: str, end: int) -> bool:
    """Is the clock phrase immediately followed by ``ครึ่ง`` (half past)?"""
    rest = expr[end:]
    return bool(re.match(r"\s*ครึ่ง", rest))


def _clock_to_24h(hour_word: str, period: str, base: int) -> int | None:
    n = _num(hour_word)
    return base + n


def normalize_clock(expr: str) -> str:
    """Rewrite Thai analog-clock vocabulary in ``expr`` to a 24h ``HH:MM``.

    Only the first recognised clock phrase is rewritten (a single line carries
    at most one time). Unrecognised text is returned unchanged.
    """

    # Ordered rules. Each returns (hour, minute_base) via a closure.
    # We handle the number + unit, then look ahead for ครึ่ง (half → :30).

    # เที่ยงคืน (midnight) must come before เที่ยง (noon).
    rules: list[tuple[re.Pattern[str], object]] = []

    def rule(pattern: str, fn):
        rules.append((re.compile(pattern), fn))

    # เที่ยงคืน → 00:00
    rule(r"เที่ยงคืน", lambda m: (0, 0))
    # เที่ยง / เที่ยงวัน → 12:00
    rule(r"เที่ยง(?:วัน)?", lambda m: (12, 0))

    # ตี N (1–5) → 0N:00  (early morning)
    rule(
        rf"ตี\s*({_NUM})",
        lambda m: (_num(m.group(1)), 0) if 1 <= _num(m.group(1)) <= 5 else None,
    )

    # บ่าย N โมง → (12+N):00  (บ่ายโมง = 13:00; บ่าย 3 โมง = 15:00)
    rule(
        rf"บ่าย\s*({_NUM})\s*โมง",
        lambda m: (12 + _num(m.group(1)), 0),
    )
    # บ่ายโมง (no number) → 13:00
    rule(r"บ่าย\s*โมง", lambda m: (13, 0))

    # N โมงเย็น (4–6) → (12+N):00
    rule(
        rf"({_NUM})\s*โมงเย็น",
        lambda m: (12 + _num(m.group(1)), 0),
    )
    # N โมงเช้า (6–11) → N:00
    rule(
        rf"({_NUM})\s*โมงเช้า",
        lambda m: (_num(m.group(1)), 0),
    )
    # N ทุ่ม (1–6) → (18+N):00
    rule(
        rf"({_NUM})\s*ทุ่ม",
        lambda m: (18 + _num(m.group(1)), 0) if 1 <= _num(m.group(1)) <= 6 else None,
    )
    # N นาฬิกา → N:00 (24h literal)
    rule(
        rf"({_NUM})\s*นาฬิกา",
        lambda m: (_num(m.group(1)) % 24, 0),
    )
    # ambiguous bare "N โมง": morning reading if 6–11, else literal N
    rule(
        rf"({_NUM})\s*โมง",
        lambda m: (_num(m.group(1)), 0),
    )
    # bare "โมง" with no number → 09:00 is NOT safe; decline (leave for day-only)
    # (intentionally no rule so "โมง" alone stays unresolved time-wise)

    for pattern, fn in rules:
        m = pattern.search(expr)
        if not m:
            continue
        result = fn(m)
        if result is None:
            continue
        hour, minute = result
        # Look for ครึ่ง (half past) directly after the phrase.
        if _half_after(expr, m.end()):
            minute = 30
            half = re.match(r"\s*ครึ่ง", expr[m.end() :])
            end = m.end() + half.end()
        else:
            end = m.end()
        replacement = _hhmm(hour, minute)
        return expr[: m.start()] + replacement + expr[end:]

    return expr


# ---------------------------------------------------------------------------
# Relative-day normalisation
# ---------------------------------------------------------------------------

_WEEKDAYS: dict[str, int] = {
    "จันทร์": 0,
    "อังคาร": 1,
    "พุธ": 2,
    "พฤหัสบดี": 3,
    "พฤหัส": 3,
    "ศุกร์": 4,
    "เสาร์": 5,
    "อาทิตย์": 6,
}


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


def normalize_day(expr: str, now: datetime) -> str:
    """Rewrite Thai relative-day phrases in ``expr`` to an explicit ISO date.

    ``now`` is Asia/Bangkok-aware; day math is done on its local date. Only the
    first recognised day phrase is rewritten. Unrecognised text is untouched.
    """
    today = now

    def shift(days: int) -> str:
        return _iso(today + timedelta(days=days))

    # Order matters: longer / more specific phrases first.
    # เมื่อวานซืน (-2) before เมื่อวาน (-1).
    patterns: list[tuple[str, object]] = [
        (r"เมื่อวานซืน", lambda m: shift(-2)),
        (r"เมื่อวาน(?:นี้)?", lambda m: shift(-1)),
        (r"(?:วัน)?มะรืน(?:นี้)?", lambda m: shift(2)),
        # วันนี้ / พรุ่งนี้ computed to explicit ISO too (never hand a bare Thai
        # day-word to dateparser when a clock may be glued to it).
        (r"วันนี้", lambda m: shift(0)),
        (r"พรุ่งนี้", lambda m: shift(1)),
        # <N> วันก่อน / <N> วันที่แล้ว  → -N
        (
            rf"({_NUM})\s*วัน(?:ก่อน|ที่แล้ว)",
            lambda m: shift(-_num(m.group(1))),
        ),
        # อีก <N> วัน → +N  (also handled by dateparser, but keep deterministic)
        (rf"อีก\s*({_NUM})\s*วัน", lambda m: shift(_num(m.group(1)))),
        # สัปดาห์หน้า / อาทิตย์หน้า → +7
        (r"(?:สัปดาห์|อาทิตย์)หน้า", lambda m: shift(7)),
        # สัปดาห์ที่แล้ว / อาทิตย์ที่แล้ว → -7
        (r"(?:สัปดาห์|อาทิตย์)ที่แล้ว", lambda m: shift(-7)),
        # ต้นเดือนหน้า → first day of next month
        (r"ต้นเดือนหน้า", lambda m: _first_of_next_month(today)),
        # สิ้นเดือน(นี้) → last day of current month
        (r"สิ้นเดือน(?:นี้)?", lambda m: _last_of_month(today)),
        # วัน<weekday>หน้า → next weekday strictly after now
        (
            rf"วัน({'|'.join(_WEEKDAYS)})หน้า",
            lambda m: _next_weekday(today, _WEEKDAYS[m.group(1)]),
        ),
    ]

    for pattern, fn in patterns:
        m = re.compile(pattern).search(expr)
        if not m:
            continue
        result = fn(m)
        if result is None:
            continue
        return expr[: m.start()] + result + expr[m.end() :]

    return expr


def _first_of_next_month(now: datetime) -> str:
    year = now.year + (1 if now.month == 12 else 0)
    month = 1 if now.month == 12 else now.month + 1
    return f"{year:04d}-{month:02d}-01"


def _last_of_month(now: datetime) -> str:
    last = calendar.monthrange(now.year, now.month)[1]
    return f"{now.year:04d}-{now.month:02d}-{last:02d}"


def _next_weekday(now: datetime, weekday: int) -> str:
    # Strictly after now: 1..7 days ahead.
    delta = (weekday - now.weekday()) % 7
    delta = 7 if delta == 0 else delta
    return _iso(now + timedelta(days=delta))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

# Invisible characters that would otherwise split/glue tokens and break matching:
# zero-width space/non-joiner/joiner and the BOM/zero-width-no-break-space.
_INVISIBLE = re.compile(r"[​‌‍﻿]")

# Thai time-prepositions ("at"/"around"/"during") carry no scheduling meaning and
# dateparser treats a glued token like "ตื่นเวลา" or "วันนี้เวลา" as a word it
# cannot parse — poisoning the whole phrase. Drop them wherever they appear,
# including glued to a preceding token with no space, leaving a single space so
# the surrounding day/clock tokens parse cleanly.
_TIME_PREPOSITIONS = re.compile(r"(?:เวลา|ตอน|ช่วง)")

# A computed ISO date immediately followed by an HH:MM with no gap
# (e.g. "2026-11-17" + "15:00" → "2026-11-1715:00"); force exactly one space.
_ISO_CLOCK_GLUE = re.compile(r"(\d{4}-\d{2}-\d{2})(\d{1,2}:\d{2})")

# The resolved date/time span we produce: an ISO date and/or an HH:MM (in that
# order, space-separated). Once we have computed one, any surrounding leftover
# word (a title fragment the SLM failed to strip, e.g. "ตื่น 08:00") makes
# dateparser return None — it parses whole strings, not spans. If we produced a
# clean span, isolate it so the remaining junk cannot poison the parse.
_RESOLVED_SPAN = re.compile(r"(?:\d{4}-\d{2}-\d{2})(?:\s+\d{1,2}:\d{2})?|\d{1,2}:\d{2}")


def normalize_thai(expr: str, *, now: datetime) -> str:
    """Normalise Thai date/clock vocabulary to dateparser-friendly forms.

    ``now`` must be Asia/Bangkok-aware (the anchor ``resolve_date`` receives).
    Returns a rewritten string; unrecognised input is returned unchanged.
    """
    if not expr:
        return expr

    # 1. Strip invisible chars and collapse whitespace runs to a single space.
    out = _INVISIBLE.sub("", expr)
    out = re.sub(r"\s+", " ", out).strip()
    # 2. Drop time-prepositions (glued or spaced) → single space, then re-collapse.
    out = _TIME_PREPOSITIONS.sub(" ", out)
    out = re.sub(r"\s+", " ", out).strip()
    # 3. Compute relative-day words to ISO, then clock words to HH:MM.
    out = normalize_day(out, now)
    out = normalize_clock(out)
    # 4. Guarantee exactly one space between a computed ISO date and its clock.
    out = _ISO_CLOCK_GLUE.sub(r"\1 \2", out)
    # 5. If we computed a clean ISO-date/HH:MM span but non-date words remain
    #    around it (title fragments the SLM left in), isolate the span so they
    #    don't poison dateparser. Only do this when we actually produced a span
    #    AND there is extra text — otherwise leave input untouched (idempotent on
    #    "07:00", "7am", English, bare Thai day words dateparser handles).
    span = _RESOLVED_SPAN.search(out)
    if span and out.strip() != span.group(0):
        out = span.group(0)
    return out
