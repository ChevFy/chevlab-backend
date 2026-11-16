"""Planner HTTP surface — 9 private endpoints under ``/api/planner`` (stage 04).

Every route is gated by the existing JWT auth (``get_current_user``) at the router
level (FR-21/22). The router is thin: validate → call service/parser → serialize.
"""

from datetime import UTC, date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import AwareDatetime

from modules.auth.jwt import get_current_user

from .deps import ParserDep
from .parser.base import ParseFailed, ParserError, ParserUnavailable, ParseTimeout
from .schemas import (
    EventList,
    ReminderFeed,
    ReminderOut,
    TodoCreate,
    TodoCreated,
    TodoList,
    TodoOut,
    TodoParse,
    TodoUpdate,
)
from .service import (
    ParseAmbiguous,
    ReminderNotFired,
    ReminderNotFound,
    TodoNotFound,
    acknowledge_reminder,
    create_todo,
    delete_todo,
    get_todo,
    list_events,
    list_todos,
    parse_and_create,
    reminder_feed,
    update_todo,
)

planner_router = APIRouter(
    prefix="/api/planner",
    tags=["planner"],
    dependencies=[Depends(get_current_user)],  # gate every route (FR-21)
)


# --------------------------------------------------------------------------
# To-dos
# --------------------------------------------------------------------------


@planner_router.post(
    "/todos", status_code=status.HTTP_201_CREATED, response_model=TodoCreated
)
async def post_todo(body: TodoCreate) -> TodoCreated:
    return await create_todo(body)


@planner_router.post(
    "/todos/parse", status_code=status.HTTP_201_CREATED, response_model=None
)
async def post_todo_parse(body: TodoParse, parser: ParserDep):
    try:
        return await parse_and_create(body.text, parser)
    except ParseAmbiguous as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "parse_ambiguous",
                "message": (
                    "Could not confidently resolve the date/time. "
                    "Please confirm or edit."
                ),
                "candidate": exc.candidate.model_dump(mode="json"),
            },
        ) from None
    except ParseTimeout:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "parse_timeout",
                "message": "The parser timed out. Retry or use structured create.",
            },
        ) from None
    except ParseFailed:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "parse_failed",
                "message": (
                    "The parser produced no usable structure. "
                    "Use structured create or retry."
                ),
            },
        ) from None
    except (ParserUnavailable, ParserError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "parser_unavailable",
                "message": (
                    "The natural-language parser is unavailable. "
                    "Use structured create or retry."
                ),
            },
        ) from None


@planner_router.get("/todos", response_model=TodoList)
async def get_todos(
    done: Annotated[bool | None, Query()] = None,
    date_filter: Annotated[date | None, Query(alias="date")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TodoList:
    return await list_todos(done=done, day=date_filter, limit=limit, offset=offset)


@planner_router.get("/todos/{todo_id}", response_model=TodoOut)
async def get_one_todo(todo_id: int) -> TodoOut:
    try:
        return await get_todo(todo_id)
    except TodoNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found"
        ) from None


@planner_router.patch("/todos/{todo_id}", response_model=TodoCreated)
async def patch_todo(todo_id: int, body: TodoUpdate) -> TodoCreated:
    try:
        return await update_todo(todo_id, body)
    except TodoNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found"
        ) from None
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None


@planner_router.delete("/todos/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_one_todo(todo_id: int) -> Response:
    try:
        await delete_todo(todo_id)
    except TodoNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found"
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------
# Events (calendar grid)
# --------------------------------------------------------------------------


@planner_router.get("/events", response_model=EventList)
async def get_events(
    window_from: Annotated[AwareDatetime, Query(alias="from")],
    window_to: Annotated[AwareDatetime, Query(alias="to")],
) -> EventList:
    if window_to < window_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'to' must be >= 'from'",
        )
    return await list_events(window_from.astimezone(UTC), window_to.astimezone(UTC))


# --------------------------------------------------------------------------
# Reminders (in-app feed + acknowledge)
# --------------------------------------------------------------------------


@planner_router.get("/reminders", response_model=ReminderFeed)
async def get_reminders(
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ReminderFeed:
    return await reminder_feed(limit)


@planner_router.post("/reminders/{reminder_id}/ack", response_model=ReminderOut)
async def post_reminder_ack(reminder_id: int) -> ReminderOut:
    try:
        return await acknowledge_reminder(reminder_id)
    except ReminderNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reminder not found"
        ) from None
    except ReminderNotFired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reminder has not fired yet",
        ) from None
