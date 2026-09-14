"""COW workspace freeze (T2.12, §5.2.2 point 3).

At the commit boundary the per-session overlay is frozen into an immutable
workspace manifest: path, size and SHA-256 of every object. The manifest
is the only durable survivor of the disposable overlay; the host directory
itself is destroyed with the session.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.artifacts import ORMWorkspaceEntry, ORMWorkspaceManifest

MAX_ENTRY_PATHS = 10_000


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def compute_root_sha256(entries: list[tuple[str, int, str]]) -> str:
    """Deterministic hash over the sorted (path, size, sha256) rows."""
    h = hashlib.sha256()
    for path, size, sha in sorted(entries):
        h.update(f"{path}\x00{size}\x00{sha}\n".encode())
    return h.hexdigest()


async def freeze_workspace(
    db: AsyncSession, session_id: uuid.UUID, workspace_dir: Path
) -> ORMWorkspaceManifest:
    """Walk the overlay and record an immutable manifest (caller's
    transaction). Raises on an unreasonably large overlay."""
    root = workspace_dir.resolve()
    entries: list[tuple[str, int, str]] = []
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(root).as_posix()
            entries.append((rel, path.stat().st_size, _sha256_file(path)))
            if len(entries) > MAX_ENTRY_PATHS:
                raise RuntimeError(f"workspace too large to freeze: >{MAX_ENTRY_PATHS} entries")

    manifest = ORMWorkspaceManifest(
        session_id=session_id,
        root_sha256=compute_root_sha256(entries),
        entry_count=len(entries),
        total_size=sum(size for _, size, _ in entries),
    )
    db.add(manifest)
    await db.flush()
    for rel, size, sha in entries:
        db.add(ORMWorkspaceEntry(manifest_id=manifest.id, path=rel, size=size, sha256=sha))
    await db.flush()
    return manifest
