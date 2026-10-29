from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from core.database import close_db
from modules.system.router import system_router
from modules.health.router import health_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_db()


app = FastAPI(title="Chevlab API", lifespan=lifespan)

app.include_router(system_router)
app.include_router(health_router)


if __name__ == "__main__":
    uvicorn.run("main:app", host="localhost", port=8000, reload=True)
