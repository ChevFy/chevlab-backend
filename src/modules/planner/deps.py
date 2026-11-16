"""Shared dependencies for the planner routes.

- ``CurrentUser`` aliases the existing JWT auth dependency so the router never
  imports a provider directly and every route is gated the same way (NFR-11).
- ``get_parser`` returns the configured ``Parser`` impl. The router calls it only
  on the ``/parse`` path; a parser outage is signalled by the parser's own typed
  exceptions at call time, keeping CRUD routes untouched (NFR-14).
"""

from typing import Annotated

from fastapi import Depends

from modules.auth.jwt import get_current_user

from .parser.base import Parser
from .parser.factory import get_configured_parser

CurrentUser = Annotated[str, Depends(get_current_user)]


def get_parser() -> Parser:
    return get_configured_parser()


ParserDep = Annotated[Parser, Depends(get_parser)]
