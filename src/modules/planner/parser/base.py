"""Swappable NL parser interface (FR-3b).

The ``Parser`` protocol has a single async method, ``extract``, which returns a
``RawExtraction`` carrying ONLY what an LLM should produce: a ``title`` and
free-text date/reminder expressions. The SLM never emits a resolved timestamp
(FR-3a) — that is the resolver's job (``resolver.py``).

Nothing outside this subpackage imports a specific provider (ollama/httpx/
anthropic); those imports live in the impl files only (NFR-13).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class RawExtraction:
    """The raw, un-resolved output of an NL extraction."""

    title: str
    date_expr: str | None = None
    reminder_expr: str | None = None
    notes: str | None = None


class ParserError(Exception):
    """Base class for parser failures."""


class ParserUnavailable(ParserError):
    """Provider is down or not configured (→ HTTP 503 parser_unavailable)."""


class ParseTimeout(ParserError):
    """Provider did not respond within the bounded timeout (→ 503 parse_timeout)."""


class ParseFailed(ParserError):
    """Provider ran but returned no usable structure (→ 503 parse_failed)."""


@runtime_checkable
class Parser(Protocol):
    async def extract(self, text: str, *, now: datetime) -> RawExtraction:
        """Extract title + rough date/reminder expressions from raw text.

        ``now`` is the Asia/Bangkok "today" context so relative expressions
        resolve correctly downstream (FR-3). Implementations must run off the
        event loop (async I/O or thread offload) and enforce a bounded timeout.
        """
        ...

    async def available(self) -> bool:
        """Return True if the provider is reachable/configured."""
        ...
