"""
AgentCast Developer Portal router.

API endpoints:
  GET /v1/interviews?agent_id={id}&limit=20&offset=0  — list interviews for agent
  GET /v1/feed?limit=20&offset=0                       — public episode feed
  GET /v1/agent/{agent_id}/public                      — public agent info (no callback_url)

Page routes (Jinja2 templates):
  GET /            — home.html
  GET /register    — register.html
  GET /agent/{id}  — dashboard.html
  GET /feed        — feed.html
"""
import os
import uuid
from pathlib import Path
from typing import Optional
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, Header, Request, Query
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, distinct, and_

from backend.db import get_db, Agent, Interview, InterviewMessage, Transcript
from backend.interviews.auth import get_admin, validate_dashboard_token

# skill.md lives at project root (one level above backend/)
_SKILL_MD = os.path.join(os.path.dirname(__file__), "..", "..", "skill.md")

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=_TEMPLATE_DIR)

router = APIRouter(tags=["portal"])


class RequestInterviewDashboard(BaseModel):
    agent_id: str
    token: str
    topic: Optional[str] = None
    github_repo_url: Optional[str] = None


# ── API endpoints ─────────────────────────────────────────────────────────────

@router.get("/v1/interviews")
async def list_interviews(
    agent_id: str,
    token: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """List interviews for a given agent. Requires dashboard token authentication.

    Token can be provided via:
    - Query param: ?token=XXX
    - Authorization header: Bearer XXX
    """
    # Extract token from Authorization header or query param
    token_to_check = token
    if not token_to_check and authorization:
        # Extract from "Bearer XXX" format
        if authorization.startswith("Bearer "):
            token_to_check = authorization[7:]

    if not token_to_check:
        raise HTTPException(status_code=401, detail="Dashboard token required (use ?token=XXX or Authorization: Bearer XXX)")

    # Validate token
    await validate_dashboard_token(agent_id, token_to_check, db)
    # Total count
    count_result = await db.execute(
        select(func.count(Interview.interview_id)).where(Interview.agent_id == agent_id)
    )
    total = count_result.scalar_one()

    # Interviews with transcript existence check via subquery
    interviews_result = await db.execute(
        select(Interview)
        .where(Interview.agent_id == agent_id)
        .order_by(Interview.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    interviews = interviews_result.scalars().all()

    # Collect interview UUIDs to batch-check transcripts
    interview_ids = [i.interview_id for i in interviews]
    transcript_set: set = set()
    if interview_ids:
        t_result = await db.execute(
            select(Transcript.interview_id).where(
                Transcript.interview_id.in_(interview_ids)
            )
        )
        transcript_set = {row[0] for row in t_result.all()}

    def _topic(interview: Interview) -> str:
        if interview.github_repo_url:
            # Extract repo name from URL e.g. https://github.com/owner/repo -> repo
            parts = interview.github_repo_url.rstrip("/").split("/")
            return parts[-1] if parts else interview.github_repo_url
        return "General Interview"

    items = []
    for i in interviews:
        # Validate episode file exists and has valid size
        episode_path = i.episode_path
        if episode_path:
            ep_file = Path("/app/episodes") / episode_path
            if not ep_file.exists() or ep_file.stat().st_size < 1000:
                episode_path = None  # Mark as unavailable

        items.append({
            "interview_id": str(i.interview_id),
            "status": i.status,
            "topic": _topic(i),
            "github_repo_url": i.github_repo_url,
            "created_at": i.created_at.isoformat() if i.created_at else None,
            "completed_at": i.completed_at.isoformat() if i.completed_at else None,
            "has_transcript": i.interview_id in transcript_set,
            "episode_path": episode_path,
            "title": i.title,
        })

    return {"agent_id": agent_id, "interviews": items, "total": total}


@router.get("/v1/feed")
async def get_feed(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Public episode feed: COMPLETED interviews, most recent first."""
    # Total COMPLETED interviews
    count_result = await db.execute(
        select(func.count(Interview.interview_id)).where(
            Interview.status == "COMPLETED"
        )
    )
    total = count_result.scalar_one()

    # Total distinct agents
    agents_result = await db.execute(
        select(func.count(distinct(Agent.agent_id)))
    )
    total_agents = agents_result.scalar_one()

    # Total interviews (all statuses)
    all_interviews_result = await db.execute(
        select(func.count(Interview.interview_id))
    )
    total_interviews = all_interviews_result.scalar_one()

    # Fetch COMPLETED interviews
    interviews_result = await db.execute(
        select(Interview)
        .where(Interview.status == "COMPLETED")
        .order_by(Interview.completed_at.desc())
        .limit(limit)
        .offset(offset)
    )
    interviews = interviews_result.scalars().all()

    interview_ids = [i.interview_id for i in interviews]

    # Batch-check transcripts
    transcript_set: set = set()
    if interview_ids:
        t_result = await db.execute(
            select(Transcript.interview_id).where(
                Transcript.interview_id.in_(interview_ids)
            )
        )
        transcript_set = {row[0] for row in t_result.all()}

    # Batch turn counts from interview_messages
    turn_counts: dict = {}
    if interview_ids:
        tc_result = await db.execute(
            select(
                InterviewMessage.interview_id,
                func.count(InterviewMessage.message_id).label("cnt"),
            )
            .where(InterviewMessage.interview_id.in_(interview_ids))
            .group_by(InterviewMessage.interview_id)
        )
        for row in tc_result.all():
            # Each turn = 1 HOST + 1 AGENT message; divide by 2
            turn_counts[row[0]] = max(1, row[1] // 2)

    def _topic(interview: Interview) -> str:
        if interview.topic:
            return interview.topic
        if interview.github_repo_url:
            parts = interview.github_repo_url.rstrip("/").split("/")
            return parts[-1] if parts else interview.github_repo_url
        return "General Interview"

    episodes = []
    for i in interviews:
        # Validate episode file exists and has valid size
        episode_path = i.episode_path
        if episode_path:
            ep_file = Path("/app/episodes") / episode_path
            if not ep_file.exists() or ep_file.stat().st_size < 1000:
                episode_path = None  # Mark as unavailable

        episodes.append({
            "interview_id": str(i.interview_id),
            "agent_id": i.agent_id,
            "topic": _topic(i),
            "completed_at": i.completed_at.isoformat() if i.completed_at else None,
            "episode_path": episode_path,
            "title": i.title,
            "has_transcript": i.interview_id in transcript_set,
            "turn_count": turn_counts.get(i.interview_id, 0),
        })

    return {
        "episodes": episodes,
        "total": total,
        "total_agents": total_agents,
        "total_interviews": total_interviews,
    }


@router.get("/v1/agents")
async def list_agents(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _admin: str = Depends(get_admin),
):
    """List all registered agents with their latest interview status. Admin only."""

    # Total count
    count_result = await db.execute(select(func.count(Agent.agent_id)))
    total = count_result.scalar_one()

    agents_result = await db.execute(
        select(Agent).order_by(Agent.created_at.desc()).limit(limit).offset(offset)
    )
    agents = agents_result.scalars().all()

    # Batch-fetch latest interview per agent
    agent_ids = [a.agent_id for a in agents]
    latest_interviews: dict = {}
    if agent_ids:
        # Subquery: max created_at per agent
        from sqlalchemy import and_ as _and
        sub = (
            select(
                Interview.agent_id,
                func.max(Interview.created_at).label("max_created"),
            )
            .where(Interview.agent_id.in_(agent_ids))
            .group_by(Interview.agent_id)
            .subquery()
        )
        iv_result = await db.execute(
            select(Interview).join(
                sub,
                _and(
                    Interview.agent_id == sub.c.agent_id,
                    Interview.created_at == sub.c.max_created,
                ),
            )
        )
        for iv in iv_result.scalars().all():
            latest_interviews[iv.agent_id] = {
                "interview_id": str(iv.interview_id),
                "status": iv.status,
                "github_repo_url": iv.github_repo_url,
            }

    items = [
        {
            "agent_id": a.agent_id,
            "agent_id_short": a.agent_id[:16],
            "display_name": a.display_name,
            "status": a.status,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "latest_interview": latest_interviews.get(a.agent_id),
        }
        for a in agents
    ]

    return {"agents": items, "total": total}


@router.get("/v1/agent/{agent_id}/public")
async def get_agent_public(
    agent_id: str,
    token: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """Public agent info — no callback_url exposed. Requires dashboard token."""
    # Extract token from Authorization header or query param
    token_to_check = token
    if not token_to_check and authorization:
        # Extract from "Bearer XXX" format
        if authorization.startswith("Bearer "):
            token_to_check = authorization[7:]

    if not token_to_check:
        raise HTTPException(status_code=401, detail="Dashboard token required (use ?token=XXX or Authorization: Bearer XXX)")

    result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    return {
        "agent_id": agent_id,
        "status": agent.status,
        "mode": "pull",
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
    }


@router.post("/v1/dashboard/request-interview")
async def dashboard_request_interview(
    body: RequestInterviewDashboard,
    db: AsyncSession = Depends(get_db),
):
    """Trigger a new interview for an agent via the dashboard.
    
    Requires valid dashboard token.
    """
    # 1. Validate token
    await validate_dashboard_token(body.agent_id, body.token, db)

    # 2. Check for existing active interview
    existing_result = await db.execute(
        select(Interview).where(
            and_(
                Interview.agent_id == body.agent_id,
                Interview.status.in_(["QUEUED", "IN_PROGRESS"]),
            )
        ).order_by(Interview.created_at.desc()).limit(1)
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        return {"status": "already_active", "interview_id": str(existing.interview_id)}

    # 3. Create new interview
    interview = Interview(
        interview_id=uuid.uuid4(),
        agent_id=body.agent_id,
        status="QUEUED",
        topic=body.topic or "Web Dashboard Request",
        github_repo_url=body.github_repo_url,
    )
    db.add(interview)
    await db.commit()

    return {"status": "QUEUED", "interview_id": str(interview.interview_id)}


# ── skill.md ──────────────────────────────────────────────────────────────────

@router.get("/skill.md", response_class=PlainTextResponse)
async def skill_md():
    """Serve the AgentCast integration spec as plain text (like moltbook.com/skill.md)."""
    path = os.path.abspath(_SKILL_MD)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="skill.md not found")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return PlainTextResponse(content, media_type="text/plain; charset=utf-8")


# ── Page routes ───────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@router.get("/agent/{agent_id}", response_class=HTMLResponse)
async def dashboard(request: Request, agent_id: str):
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "agent_id": agent_id}
    )


@router.get("/feed", response_class=HTMLResponse)
async def feed_page(request: Request):
    return templates.TemplateResponse("feed.html", {"request": request})


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})
