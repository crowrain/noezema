"""Tests for SQLAlchemy ORM models — verify schema consistency."""

import uuid
from datetime import datetime

from packages.domain.models.base import Base
from packages.domain.models.orm_session import (
    ORMSession, ORMQuestion, ORMAction, ORMModelRun, ORMAuditEvent,
)
from packages.domain.models.orm_claim import (
    ORMClaim, ORMEvidence, ORMClaimAssessment, ORMClaimAssessmentHead,
    ORMConfigSnapshot, ORMDomainRevision,
)
from packages.domain.models.enums import SessionState, EffectiveGrade, EpistemicStatus


def test_session_creation():
    s = ORMSession(id=uuid.uuid4(), state=SessionState.WAKING.value)
    assert s.state == SessionState.WAKING.value
    assert s.created_at is None  # set by server_default on insert
    assert s.updated_at is None


def test_question_creation():
    q = ORMQuestion(
        id=uuid.uuid4(),
        statement="Is Docker faster than systemd-nspawn?",
        source="seeded",
    )
    assert q.statement
    assert not q.resolved


def test_action_creation():
    a = ORMAction(
        session_id=uuid.uuid4(),
        tool="workspace.read",
        arguments='{"path": "README.md"}',
        status="completed",
        step=0,
    )
    assert a.tool == "workspace.read"
    assert a.step == 0


def test_claim_and_evidence():
    claim = ORMClaim(
        id=uuid.uuid4(),
        statement="Docker uses overlayfs",
        claim_type="external_fact",
        freshness_status="fresh",
    )
    ev = ORMEvidence(
        claim_id=claim.id,
        relation="supports",
        evidence_kind="source_assertion",
        identity_hash="abc123",
    )
    assert ev.claim_id == claim.id


def test_assessment_creation():
    a = ORMClaimAssessment(
        claim_id=uuid.uuid4(),
        effective_grade=EffectiveGrade.E2.value,
        epistemic_status=EpistemicStatus.SUPPORTED.value,
        rules_hash="rule_sha256",
        evidence_set_hash="ev_sha256",
        confidence=0.6,
    )
    assert a.effective_grade == "E2"
    assert a.confidence == 0.6


def test_config_snapshot():
    cs = ORMConfigSnapshot(
        version=1,
        rules_hash="sha256",
        activation_state="active",
        scope="default",
    )
    assert cs.version == 1


def test_domain_revision():
    dr = ORMDomainRevision(scope="default", value=0)
    assert dr.scope == "default"
    assert dr.value == 0


def test_audit_event():
    ae = ORMAuditEvent(
        event_type="session_started",
        session_id=uuid.uuid4(),
        payload='{"question": "test"}',
    )
    assert ae.event_type == "session_started"
