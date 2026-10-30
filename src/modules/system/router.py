from typing import Annotated

from fastapi import APIRouter, Query, status

from .service import read_history, read_info, read_latest

system_router = APIRouter(prefix="/api", tags=["system"])


@system_router.get("/info", status_code=status.HTTP_200_OK)
def get_info():
    return read_info()


@system_router.get("/latest", status_code=status.HTTP_200_OK)
async def get_latest():
    return await read_latest()


@system_router.get(
    "/history",
    status_code=status.HTTP_200_OK,
)
async def get_history(hours: Annotated[int, Query(ge=1, le=168)] = 1):
    return await read_history(hours)
