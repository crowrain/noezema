"""Scenario: core domain tables via ORM + repositories (T1.4-T1.6, §14)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from packages.domain.db.uow import transaction
from packages.domain.models.enums import (
    ActionState,
    AuditEventType,
    IdempotencyClass,
    OperatorCommandType,
    QuestionOrigin,
    QuestionState,
    SessionState,
)
from packages.domain.models.events import ORMAuditEvent
from packages.domain.models.inbox import ORMMessage, ORMOperatorCommand
from packages.domain.models.questions import ORMQuestion
from packages.domain.models.sessions import ORMAction, ORMModelRun, ORMSession
from packages.domain.repositories.inbox import MessageRepository, OperatorCommandRepository
from packages.domain.repositories.questions import QuestionRepository
from packages.domain.repositories.sessions import ActionRepository, ModelRunRepository, SessionRepository
from packages.domain.services.audit import AuditService

pytestmark = [pytest.mark.scenario]


async def _bootstrap_session(db, question: ORMQuestion) -> ORMSession:
    """A session bound to the effective config (bootstrap pointer)."""
    head_query = "SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope='global'"
    head = (await db.execute(text(head_query))).scalar_one()
    session = ORMSession(state=SessionState.CREATED.value, question_id=question.id, config_snapshot_id=head)
    return await SessionRepository.create(db, session)


@pytest.mark.asyncio
async def test_full_causal_chain(migrated_db: tuple[str, object]) -> None:
    _url, engine = migrated_db
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)  # type: ignore[arg-type]
    async with factory() as db, transaction(db):
        question = await QuestionRepository.create(
            db,
            ORMQuestion(text="Что такое квантовая запутанность?", origin=QuestionOrigin.SEEDED.value),
        )
        assert question.state == QuestionState.CANDIDATE.value

        session = await _bootstrap_session(db, question)
        assert session.config_snapshot_id is not None

        run = await ModelRunRepository.create(
            db,
            ORMModelRun(
                session_id=session.id,
                turn_id=uuid.uuid4(),
                phase=SessionState.EXPLORING.value,
                model_fingerprint={"model": "fake-thinker", "prompt_version": "explorer-v1"},
                input_tokens=10,
                output_tokens=20,
                latency_ms=123.456,
            ),
        )

        action = await ActionRepository.create(
            db,
            ORMAction(
                session_id=session.id,
                model_run_id=run.id,
                idempotency_key="key-1",
                idempotency_class=IdempotencyClass.OBSERVATION.value,
                tool="web.search",
                arguments_hash="h" * 64,
                state=ActionState.STARTED.value,
            ),
        )
        assert action.id is not None

        audit = AuditService(db)
        event = await audit.record(
            AuditEventType.SESSION_STARTED,
            session_id=session.id,
            public_summary="session started",
        )
        assert event.sequence == 1
        event2 = await audit.record(
            AuditEventType.ACTION_STARTED,
            session_id=session.id,
            payload={"action_id": str(action.id)},
        )
        assert event2.sequence == 2

        message = await MessageRepository.create(
            db,
            ORMMessage(body="Расскажи подробнее", sender="owner"),
        )
        command = await OperatorCommandRepository.create(
            db,
            ORMOperatorCommand(
                actor_id="owner",
                type=OperatorCommandType.WAKE_NOW.value,
                idempotency_key="wake-1",
            ),
        )
        assert command[1] is True

    # Reload in a fresh session and verify the chain + outbox twins.
    async with factory() as db:
        question = await QuestionRepository.get(db, _id_of(question))
        assert question is not None
        session = await SessionRepository.get(db, _id_of(session))
        assert session is not None
        runs = await ModelRunRepository.list_for_session(db, _id_of(session))
        assert len(runs) == 1
        actions = await ActionRepository.list_for_session(db, _id_of(session))
        assert len(actions) == 1
        # audit sequence per session
        from packages.domain.repositories.events import AuditEventRepository

        listed = await AuditEventRepository.list_for_session(db, _id_of(session))
        assert [e.sequence for e in listed] == [1, 2]
        # outbox twins exist and are unpublished
        outbox_query = "SELECT COUNT(*) FROM outbox_events WHERE published_at IS NULL"
        outbox_rows = (await db.execute(text(outbox_query))).scalar_one()
        assert outbox_rows == 2
        # message and command persisted
        assert (await MessageRepository.get(db, _id_of(message))) is not None
        cmd = await OperatorCommandRepository.get_by_key(db, "wake-1")
        assert cmd is not None
        assert _id_of(command[0]) is not None


def _id_of(obj: object) -> uuid.UUID:
    return obj.id  # type: ignore[attr-defined,union-attr]


@pytest.mark.asyncio
async def test_causal_constraints(migrated_db: tuple[str, object]) -> None:
    _url, engine = migrated_db
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)  # type: ignore[arg-type]

    async with factory() as db:
        async with transaction(db):
            q1 = await QuestionRepository.create(db, ORMQuestion(text="q1", origin=QuestionOrigin.SEEDED.value))
            session = await _bootstrap_session(db, q1)
            turn = uuid.uuid4()
            r1 = await ModelRunRepository.create(
                db,
                ORMModelRun(
                    session_id=session.id, turn_id=turn, model_fingerprint={"m": "x"},
                ),
            )
            await ActionRepository.create(
                db,
                ORMAction(
                    session_id=session.id,
                    model_run_id=r1.id,
                    idempotency_key="k",
                    idempotency_class=IdempotencyClass.PURE.value,
                    tool="workspace.read",
                    arguments_hash="h" * 64,
                ),
            )

        # (1) one model run per turn: duplicate (session, turn) rejected
        async with factory() as db2:
            with pytest.raises(IntegrityError):
                async with transaction(db2):
                    await ModelRunRepository.create(
                        db2,
                        ORMModelRun(
                            session_id=session.id, turn_id=turn, model_fingerprint={"m": "x"},
                        ),
                    )

        # (2) one action per model run
        async with factory() as db3:
            with pytest.raises(IntegrityError):
                async with transaction(db3):
                    await ActionRepository.create(
                        db3,
                        ORMAction(
                            session_id=session.id,
                            model_run_id=r1.id,
                            idempotency_key="other",
                            idempotency_class=IdempotencyClass.PURE.value,
                            tool="workspace.read",
                            arguments_hash="h" * 64,
                        ),
                    )

        # (3) audit sequence uniqueness per session
        async with factory() as db4:
            from packages.domain.repositories.events import AuditEventRepository

            async with transaction(db4):
                await AuditEventRepository.create(
                    db4,
                    ORMAuditEvent(session_id=session.id, sequence=99, type=AuditEventType.ALERT_RAISED.value),
                )
            with pytest.raises(IntegrityError):
                async with transaction(db4):
                    await AuditEventRepository.create(
                        db4,
                        ORMAuditEvent(session_id=session.id, sequence=99, type=AuditEventType.ALERT_RAISED.value),
                    )

        # (4) operator command idempotency: replay returns existing
        async with factory() as db5, transaction(db5):
            c1, created = await OperatorCommandRepository.create(
                db5,
                ORMOperatorCommand(
                    actor_id="owner", type=OperatorCommandType.PAUSE.value, idempotency_key="dup-key",
                ),
            )
            assert created is True
            c2, created2 = await OperatorCommandRepository.create(
                db5,
                ORMOperatorCommand(
                    actor_id="owner", type=OperatorCommandType.PAUSE.value, idempotency_key="dup-key",
                ),
            )
            assert created2 is False
            assert c2.id == c1.id


@pytest.mark.asyncio
async def test_fifo_question_selection(migrated_db: tuple[str, object]) -> None:
    _url, engine = migrated_db
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)  # type: ignore[arg-type]
    async with factory() as db:
        async with transaction(db):
            low = await QuestionRepository.create(
                db, ORMQuestion(text="low", origin=QuestionOrigin.SEEDED.value, priority=1)
            )
            high = await QuestionRepository.create(
                db, ORMQuestion(text="high", origin=QuestionOrigin.MESSAGE.value, priority=10)
            )
            await QuestionRepository.set_state(db, high.id, QuestionState.SELECTED)

        candidates = await QuestionRepository.list_candidates(db)
        assert [q.text for q in candidates] == ["low"]  # selected one excluded
        assert _id_of(candidates[0]) == low.id


@pytest.mark.asyncio
async def test_audit_service_rejects_unknown_type(migrated_db: tuple[str, object]) -> None:
    _url, engine = migrated_db
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)  # type: ignore[arg-type]
    async with factory() as db:
        # closed registry: unknown type name raises built-in ValueError
        with pytest.raises(ValueError):
            await AuditService(db).record("not_a_real_event")
