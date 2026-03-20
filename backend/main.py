import os
import logging
import redis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
from backend.db import init_db
from backend.identity import router as identity_router
from backend.interviews import router as interviews_router
from backend.interviews.transcript_router import router as transcript_router
from backend.portal.router import router as portal_router

logger = logging.getLogger(__name__)


_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

templates = Jinja2Templates(directory=_TEMPLATE_DIR)

# Initialize Redis client for replay attack prevention
redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = None

try:
    redis_client = redis.from_url(redis_url)
    redis_client.ping()
    logger.info("Connected to Redis")
except redis.ConnectionError:
    logger.warning("Redis not available - replay prevention disabled")
    redis_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="AgentCast API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static assets
os.makedirs(_STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

# Serve episode MP3 files — wrapped in try/except for local dev without Docker
try:
    app.mount("/episodes", StaticFiles(directory="/app/episodes"), name="episodes")
except RuntimeError:
    # Directory does not exist (local dev without Docker) — skip mount
    pass

app.include_router(identity_router)
app.include_router(interviews_router)
app.include_router(transcript_router)
app.include_router(portal_router)

# Export redis_client for use in auth module
app.state.redis_client = redis_client


@app.get("/health")
async def health():
    return {"status": "ok"}
