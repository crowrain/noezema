"""Web API routes — backed by SQLAlchemy DB."""

from __future__ import annotations

import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from apps.web.db import get_session
from packages.domain.models.orm_session import (
    ORMSession, ORMQuestion, ORMAction, ORMAuditEvent, ORMMessage,
)
from packages.domain.models.orm_claim import ORMClaim, ORMClaimAssessment
from packages.domain.models.enums import SessionState, AuditEventType

router = APIRouter()


# ---------------------------------------------------------------------------
# Status & Health
# ---------------------------------------------------------------------------

@router.get("/")
async def status_page(db: AsyncSession = Depends(get_session)) -> dict:
    """Live status with DB counts."""
    claims_count = await db.scalar(select(func.count(ORMClaim.id)))
    questions_count = await db.scalar(
        select(func.count(ORMQuestion.id)).where(ORMQuestion.resolved.is_(False))
    )
    unresolved_sessions = await db.scalar(
        select(func.count(ORMSession.id)).where(
            ORMSession.state.notin_([
                SessionState.SUCCEEDED,
                SessionState.SUCCEEDED_PARTIAL,
                SessionState.ABORTING,
            ])
        )
    )
    return {
        "name": "noezema",
        "version": "0.1.0-dev",
        "status": "running",
        "claims_count": claims_count or 0,
        "pending_questions": questions_count or 0,
        "unresolved_sessions": unresolved_sessions or 0,
    }


# ---------------------------------------------------------------------------
# Timeline (audit events)
# ---------------------------------------------------------------------------

@router.get("/api/timeline")
async def timeline(limit: int = 50, db: AsyncSession = Depends(get_session)) -> list[dict]:
    """Recent audit events, newest first."""
    result = await db.execute(
        select(ORMAuditEvent)
        .order_by(ORMAuditEvent.created_at.desc())
        .limit(limit)
    )
    events = result.scalars().all()
    return [
        {
            "id": str(e.id),
            "event_type": e.event_type,
            "session_id": str(e.session_id) if e.session_id else None,
            "payload": e.payload,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

@router.get("/api/sessions")
async def list_sessions(
    state: str | None = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    """List sessions, optionally filtered by state."""
    q = select(ORMSession).order_by(ORMSession.created_at.desc()).limit(limit)
    if state:
        q = q.where(ORMSession.state == state)
    result = await db.execute(q)
    sessions = result.scalars().all()
    return [
        {
            "id": str(s.id),
            "state": s.state,
            "question_id": str(s.question_id) if s.question_id else None,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "last_progress_at": s.last_progress_at.isoformat() if s.last_progress_at else None,
        }
        for s in sessions
    ]


@router.get("/api/sessions/{session_id}")
async def get_session_detail(
    session_id: str,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Session detail with actions and claims summary."""
    sid = uuid.UUID(session_id)
    session = await db.get(ORMSession, sid)
    if session is None:
        raise HTTPException(404, "Session not found")

    # Actions count
    actions_count = await db.scalar(
        select(func.count(ORMAction.id)).where(ORMAction.session_id == sid)
    )

    # Claims count for this session
    claims_count = await db.scalar(
        select(func.count(ORMClaim.id)).where(ORMClaim.created_in_session == sid)
    )

    return {
        "id": str(session.id),
        "state": session.state,
        "question_id": str(session.question_id) if session.question_id else None,
        "actions_count": actions_count or 0,
        "claims_count": claims_count or 0,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "last_heartbeat_at": session.last_heartbeat_at.isoformat() if session.last_heartbeat_at else None,
        "last_progress_at": session.last_progress_at.isoformat() if session.last_progress_at else None,
    }


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------

@router.get("/api/claims")
async def list_claims(
    limit: int = 50,
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    """List claims with assessment info."""
    result = await db.execute(
        select(ORMClaim)
        .order_by(ORMClaim.created_at.desc())
        .limit(limit)
    )
    claims = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "statement": c.statement,
            "claim_type": c.claim_type,
            "freshness_status": c.freshness_status,
            "created_in_session": str(c.created_in_session) if c.created_in_session else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in claims
    ]


@router.get("/api/claims/{claim_id}")
async def get_claim(
    claim_id: str,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Claim detail with latest assessment."""
    cid = uuid.UUID(claim_id)
    claim = await db.get(ORMClaim, cid)
    if claim is None:
        raise HTTPException(404, "Claim not found")

    # Latest assessment
    assessment = await db.scalar(
        select(ORMClaimAssessment)
        .where(ORMClaimAssessment.claim_id == cid)
        .where(ORMClaimAssessment.valid.is_(True))
        .order_by(ORMClaimAssessment.created_at.desc())
        .limit(1)
    )

    return {
        "id": str(claim.id),
        "statement": claim.statement,
        "claim_type": claim.claim_type,
        "freshness_status": claim.freshness_status,
        "created_in_session": str(claim.created_in_session) if claim.created_in_session else None,
        "assessment": {
            "effective_grade": assessment.effective_grade,
            "epistemic_status": assessment.epistemic_status,
            "confidence": assessment.confidence,
        } if assessment else None,
        "created_at": claim.created_at.isoformat() if claim.created_at else None,
    }


# ---------------------------------------------------------------------------
# Messages (inbox)
# ---------------------------------------------------------------------------

@router.post("/api/message")
async def send_message(
    sender: str,
    body: str,
    priority: str = "normal",
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Send a message to the thinker — stored in DB."""
    msg = ORMMessage(
        sender=sender,
        body=body,
        priority=priority,
        state="queued",
    )
    db.add(msg)
    await db.commit()
    return {"id": str(msg.id), "state": "queued"}


@router.get("/api/messages")
async def list_messages(
    state: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    """List messages from inbox."""
    q = select(ORMMessage).order_by(ORMMessage.created_at.desc()).limit(limit)
    if state:
        q = q.where(ORMMessage.state == state)
    result = await db.execute(q)
    messages = result.scalars().all()
    return [
        {
            "id": str(m.id),
            "sender": m.sender,
            "body": m.body,
            "priority": m.priority,
            "state": m.state,
            "session_id": str(m.session_id) if m.session_id else None,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]


# ---------------------------------------------------------------------------
# Operator Commands
# ---------------------------------------------------------------------------

@router.post("/api/command")
async def operator_command(
    actor: str,
    type: str,
    arguments: dict | None = None,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Typed operator command — logged as audit event."""
    allowed = {
        "wake_now", "pause", "resume", "stop_gracefully", "abort_session",
    }
    if type not in allowed:
        raise HTTPException(400, f"Unknown command: {type}")

    event = ORMAuditEvent(
        event_type=AuditEventType.OPERATOR_COMMAND.value,
        payload=f'{{"actor": "{actor}", "type": "{type}", "arguments": {str(arguments) if arguments else "null"}}}',
    )
    db.add(event)
    await db.commit()

    idem = uuid.uuid4()
    return {"id": str(idem), "state": "accepted"}


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------

@router.get("/api/questions")
async def list_questions(
    resolved: bool | None = None,
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    """List research questions."""
    q = select(ORMQuestion).order_by(ORMQuestion.created_at.desc())
    if resolved is not None:
        q = q.where(ORMQuestion.resolved.is_(resolved))
    result = await db.execute(q)
    questions = result.scalars().all()
    return [
        {
            "id": str(q.id),
            "statement": q.statement,
            "source": q.source,
            "resolved": q.resolved,
            "created_at": q.created_at.isoformat() if q.created_at else None,
        }
        for q in questions
    ]


@router.post("/api/questions")
async def create_question(
    statement: str,
    source: str = "operator",
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Create a new question and enqueue orchestrator session via RQ."""
    question = ORMQuestion(
        statement=statement,
        source=source,
        resolved=False,
    )
    db.add(question)
    await db.commit()

    # Enqueue orchestrator session in RQ
    try:
        from redis import Redis
        from rq import Queue
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        redis_conn = Redis.from_url(redis_url)
        rq_queue = Queue("default", connection=redis_conn)
        rq_queue.enqueue(
            "apps.rq.tasks.run_session_task",
            args=[str(question.id)],
            job_id=f"session-{question.id}",
            result_ttl=86400,
        )
    except Exception:
        # Redis not available — session can be picked up manually later
        pass

    return {"id": str(question.id), "state": "queued"}

