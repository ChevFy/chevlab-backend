from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from core.database import close_db
from modules.auth.router import auth_router
from modules.health.router import health_router
from modules.scheduler.scheduler import (
    scheduler,
    scheduler_clean_system_metric,
    scheduler_store_system_metric,
)
from modules.system.router import system_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler_store_system_metric()
    scheduler_clean_system_metric()

    scheduler.start()
    yield
    scheduler.shutdown()
    await close_db()


app = FastAPI(title="Chevlab API", lifespan=lifespan)

app.include_router(system_router)
app.include_router(health_router)
app.include_router(auth_router)


if __name__ == "__main__":
    uvicorn.run("main:app", host="localhost", port=8000, reload=True)
