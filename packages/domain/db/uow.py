"""Unit-of-work helper (T1.5).

All domain writes go through one transaction per logical operation; audit
and outbox rows are added to the same transaction by the caller services
(§3.3).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def transaction(db: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Commit on success, rollback on any exception."""
    try:
        yield db
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
