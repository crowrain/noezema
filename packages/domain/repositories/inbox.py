"""Messages / operator-commands repositories (T1.5)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.base import JsonDict
from packages.domain.models.enums import MessageState
from packages.domain.models.inbox import ORMMessage, ORMOperatorCommand


class MessageRepository:
    @staticmethod
    async def create(db: AsyncSession, message: ORMMessage) -> ORMMessage:
        db.add(message)
        await db.flush()
        return message

    @staticmethod
    async def get(db: AsyncSession, message_id: uuid.UUID) -> ORMMessage | None:
        return await db.get(ORMMessage, message_id)

    @staticmethod
    async def list_pending_delivery(db: AsyncSession, limit: int = 10) -> list[ORMMessage]:
        """Created/queued messages, highest priority first (§13.6)."""
        stmt = (
            select(ORMMessage)
            .where(ORMMessage.state.in_([MessageState.CREATED.value, MessageState.QUEUED.value]))
            .order_by(ORMMessage.priority.desc(), ORMMessage.created_at.asc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def set_state(db: AsyncSession, message_id: uuid.UUID, state: MessageState) -> None:
        message = await db.get(ORMMessage, message_id)
        if message is None:
            raise LookupError(f"message {message_id} not found")
        message.state = state.value
        await db.flush()


class OperatorCommandRepository:
    @staticmethod
    async def create(db: AsyncSession, command: ORMOperatorCommand) -> tuple[ORMOperatorCommand, bool]:
        """Insert; returns (command, created). Duplicate idempotency key
        returns the existing command (idempotent replay, §13.2)."""
        existing = await OperatorCommandRepository.get_by_key(db, command.idempotency_key)
        if existing is not None:
            return existing, False
        db.add(command)
        await db.flush()
        return command, True

    @staticmethod
    async def get_by_key(db: AsyncSession, key: str) -> ORMOperatorCommand | None:
        stmt = select(ORMOperatorCommand).where(ORMOperatorCommand.idempotency_key == key)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def set_state(db: AsyncSession, command_id: uuid.UUID, state: str, result: JsonDict | None = None) -> None:
        command = await db.get(ORMOperatorCommand, command_id)
        if command is None:
            raise LookupError(f"operator command {command_id} not found")
        command.state = state
        if result is not None:
            command.result = result
        await db.flush()
