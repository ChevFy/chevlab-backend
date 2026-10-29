from fastapi import APIRouter, status, HTTPException
from core.database import SessionLocal
from sqlalchemy import text

health_router = APIRouter(prefix="/api", tags=["health"])


@health_router.get("/healthz", status_code=status.HTTP_200_OK)
def healthz():
    return {"status": "ok"}


@health_router.get("/readyz")
async def readyz():
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "db unavailable")
    return {"status": "ready"}
