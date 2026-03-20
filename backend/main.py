import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from backend.db import init_db
from backend.identity import router as identity_router
from backend.interviews import router as interviews_router
from backend.interviews.transcript_router import router as transcript_router
from backend.portal.router import router as portal_router
from backend.dashboard.router import router as dashboard_router


_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

templates = Jinja2Templates(directory=_TEMPLATE_DIR)


class HSTSMiddleware(BaseHTTPMiddleware):
    """Add HSTS and security headers (backup if nginx not configured)."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


# Custom limiter using IP address as default key
limiter = Limiter(key_func=get_remote_address)


def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Handle rate limit exceeded errors."""
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Try again later."},
    )


app = FastAPI(title="AgentCast API", version="0.1.0", lifespan=lifespan)

# Add HSTS middleware (backup security if nginx not configured)
app.add_middleware(HSTSMiddleware)

# Add rate limiting exception handler
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Store limiter in app state for use by routers
# Disable rate limiting in test mode (when TESTING env var is set)
if os.getenv('TESTING') == '1':
    app.state.limiter = None
else:
    app.state.limiter = limiter

# Configure CORS - lock down allowed origins
# Read from environment variable, or use sensible defaults for development
_cors_origins_env = os.getenv("CORS_ORIGINS", "").strip()
if _cors_origins_env:
    ALLOWED_ORIGINS = [origin.strip() for origin in _cors_origins_env.split(",") if origin.strip()]
else:
    # Default development origins
    ALLOWED_ORIGINS = [
        "https://agentcast.ai",
        "https://www.agentcast.ai",
        "http://localhost:3000",   # Dev portal
        "http://localhost:8000",   # Dev backend
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,  # Only True if using session cookies/authentication
    allow_methods=["GET", "POST", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
    max_age=3600,
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
app.include_router(dashboard_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
