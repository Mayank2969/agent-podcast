from fastapi import FastAPI
from contextlib import asynccontextmanager
from backend.db import init_db
from backend.identity import router as identity_router
from backend.interviews import router as interviews_router
from backend.interviews.transcript_router import router as transcript_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="AgentCast API", version="0.1.0", lifespan=lifespan)

app.include_router(identity_router)
app.include_router(interviews_router)
app.include_router(transcript_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
