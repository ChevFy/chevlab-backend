"""Test doubles so Ollama/Telegram are never required (stage 06 constraint)."""

from __future__ import annotations

from datetime import datetime

from modules.planner.parser.base import RawExtraction


class StubParser:
    """A ``Parser`` that returns a canned ``RawExtraction`` or raises.

    Pass ``extraction=`` for a successful extract, or ``raises=`` with a parser
    exception instance/class to simulate outage/timeout/failure. ``calls`` counts
    invocations so tests can assert exactly-one-call (NFR-3).
    """

    def __init__(self, *, extraction: RawExtraction | None = None, raises=None):
        self._extraction = extraction
        self._raises = raises
        self.calls = 0

    async def extract(self, text: str, *, now: datetime) -> RawExtraction:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        assert self._extraction is not None
        return self._extraction

    async def available(self) -> bool:
        return self._raises is None
