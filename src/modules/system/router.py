from fastapi import APIRouter, status
from .service import get_info

system_router = APIRouter(prefix="/api", tags=["system"])


@system_router.get("/get_info", status_code=status.HTTP_200_OK)
def api_get_info():
    return get_info()
