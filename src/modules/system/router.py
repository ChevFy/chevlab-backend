from fastapi import APIRouter, status

router = APIRouter(prefix="/api")


@router.get("/healthz", status_code=status.HTTP_200_OK)
def api_healthz():
    return {"status": "ok"}
