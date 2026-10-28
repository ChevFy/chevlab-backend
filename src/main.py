from fastapi import FastAPI
import uvicorn
from modules.system.router import router

app = FastAPI(title="Chevlab API")

app.include_router(router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="localhost", port=8000, reload=True)
