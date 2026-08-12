"""Materialize an immutable workspace view for one sandbox action."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from packages.artifacts.store import ContentAddressedArtifactStore
from packages.domain import ActionId
from packages.persistence.models import WorkspaceFileRecord


class WorkspaceSnapshotFactory:
    """Combine the read-only seed corpus with the canonical COW manifest."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        seed_root: Path,
        store: ContentAddressedArtifactStore,
        max_bytes: int,
    ) -> None:
        self._session_factory = session_factory
        self._seed_root = seed_root.resolve(strict=True)
        self._store = store
        self._max_bytes = max_bytes

    @contextmanager
    def materialize(self, action_id: ActionId) -> Iterator[Path]:
        views_root = self._store.root / "views"
        views_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"{action_id}.", dir=views_root) as name:
            view = Path(name)
            with self._session_factory() as db:
                rows = db.scalars(
                    select(WorkspaceFileRecord).order_by(WorkspaceFileRecord.path)
                ).all()
            total = self._seed_size_excluding({row.path for row in rows})
            total += sum(row.size_bytes for row in rows)
            if total > self._max_bytes:
                raise ValueError("workspace snapshot exceeds its policy quota")
            self._copy_seed(view)
            for row in rows:
                content = self._store.read(digest=row.content_sha256, max_bytes=row.size_bytes)
                destination = (view / row.path).resolve(strict=False)
                if not destination.is_relative_to(view):
                    raise ValueError("workspace manifest path escapes its snapshot")
                destination.parent.mkdir(parents=True, exist_ok=True)
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=f".{destination.name}.",
                    dir=destination.parent,
                )
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
            yield view

    def _copy_seed(self, view: Path) -> None:
        for source in self._seed_root.rglob("*"):
            if source.is_symlink():
                continue
            relative = source.relative_to(self._seed_root)
            destination = view / relative
            if source.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            elif source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

    def _seed_size_excluding(self, overridden: set[str]) -> int:
        total = 0
        for source in self._seed_root.rglob("*"):
            if source.is_symlink() or not source.is_file():
                continue
            relative = source.relative_to(self._seed_root).as_posix()
            if relative not in overridden:
                total += source.stat().st_size
        return total
