"""Parser selection from settings (FR-3b, NFR-7).

``get_configured_parser`` returns the active ``Parser`` impl based on
``settings.CALENDAR_PARSER``. The app always boots regardless of the provider's
state; availability is checked at request time by ``deps.get_parser`` so that an
unconfigured/unreachable parser degrades ONLY the ``/parse`` endpoint (NFR-14/17).
"""

from functools import lru_cache

from core.config import settings

from .base import Parser
from .claude_parser import ClaudeParser
from .ollama_parser import OllamaParser


@lru_cache(maxsize=1)
def get_configured_parser() -> Parser:
    if settings.CALENDAR_PARSER == "claude":
        return ClaudeParser()
    return OllamaParser()
