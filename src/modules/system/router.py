from fastapi import APIRouter, status
from .service import get_info

router = APIRouter(prefix="/api")


@router.get("/healthz", status_code=status.HTTP_200_OK)
def api_healthz():
    return {"status": "ok"}


@router.get("/get_info", status_code=status.HTTP_200_OK)
def api_get_info():
    return get_info()

