from fastapi import APIRouter, status
from .service import read_info, read_latest

system_router = APIRouter(prefix="/api", tags=["system"])


@system_router.get("/info", status_code=status.HTTP_200_OK)
def get_info():
    return read_info()


@system_router.get("/latest", status_code=status.HTTP_200_OK)
async def get_latest():
    return await read_latest()
