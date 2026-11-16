from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from core.database import SessionLocal

health_router = APIRouter(prefix="/api", tags=["health"])


@health_router.get("/healthz", status_code=status.HTTP_200_OK)
def healthz():
    return {"status": "ok"}


@health_router.get("/readyz")
async def readyz():
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — readiness probe: any DB failure means "not ready"
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db unavailable")
    return {"status": "ready"}
