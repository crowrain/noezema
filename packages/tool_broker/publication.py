"""Transactional publication of prepared workspace and artifact effects."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.artifacts import ContentAddressedArtifactStore
from packages.domain import (
    ArtifactCreatePayload,
    BoundAction,
    ToolExecutionObservation,
    WorkspaceWritePayload,
)
from packages.domain._base import JsonObject
from packages.persistence.models import (
    ArtifactBlobRecord,
    ArtifactRecord,
    WorkspaceFileRecord,
    WorkspaceVersionRecord,
)
from packages.tool_broker.errors import ToolExecutionError


class ToolEffectPublisher(Protocol):
    def publish(
        self,
        db: Session,
        *,
        action: BoundAction,
        observation: ToolExecutionObservation,
        published_at: datetime,
    ) -> JsonObject | None: ...


class WorkspaceArtifactPublisher:
    """Move a prepared immutable blob into the visible database manifest."""

    def __init__(
        self,
        *,
        store: ContentAddressedArtifactStore,
        workspace_root: Path,
        max_workspace_total_bytes: int,
        max_artifact_store_bytes: int,
    ) -> None:
        self._store = store
        self._workspace_root = workspace_root.resolve(strict=True)
        self._max_workspace_total_bytes = max_workspace_total_bytes
        self._max_artifact_store_bytes = max_artifact_store_bytes

    def publish(
        self,
        db: Session,
        *,
        action: BoundAction,
        observation: ToolExecutionObservation,
        published_at: datetime,
    ) -> JsonObject | None:
        payload = observation.payload
        if isinstance(payload, WorkspaceWritePayload):
            return self._publish_workspace(
                db,
                action=action,
                payload=payload,
                published_at=published_at,
            )
        if isinstance(payload, ArtifactCreatePayload):
            self._ensure_blob(
                db,
                digest=payload.content_sha256,
                size_bytes=payload.size_bytes,
                published_at=published_at,
            )
            self._insert_artifact(
                db,
                action=action,
                artifact_id=payload.artifact_id.root,
                digest=payload.content_sha256,
                kind="standalone",
                logical_name=payload.name,
                media_type=payload.media_type,
                published_at=published_at,
            )
            return {
                "kind": payload.kind,
                "artifact_id": str(payload.artifact_id),
                "content_sha256": payload.content_sha256,
                "size_bytes": payload.size_bytes,
            }
        return None

    def _publish_workspace(
        self,
        db: Session,
        *,
        action: BoundAction,
        payload: WorkspaceWritePayload,
        published_at: datetime,
    ) -> JsonObject:
        manifest = db.scalar(
            select(WorkspaceFileRecord)
            .where(WorkspaceFileRecord.path == payload.path)
            .with_for_update()
        )
        manifest_rows = db.scalars(select(WorkspaceFileRecord)).all()
        self._reject_path_collision(payload.path, manifest_rows)

        if manifest is None:
            previous_sha256, previous_size = self._base_file_identity(payload.path)
            revision = 1
        else:
            previous_sha256 = manifest.content_sha256
            previous_size = manifest.size_bytes
            revision = manifest.revision + 1

        expected = payload.expected_content_sha256
        matches = previous_sha256 is None if expected == "absent" else previous_sha256 == expected
        if not matches:
            raise ToolExecutionError(
                "workspace_revision_conflict",
                "The workspace file changed after the model observed it.",
                retryable=False,
            )

        current_total = self._logical_workspace_size(manifest_rows)
        next_total = current_total - previous_size + payload.size_bytes
        if next_total > self._max_workspace_total_bytes:
            raise ToolExecutionError(
                "workspace_quota_exceeded",
                "The persistent workspace would exceed its capability-policy quota.",
                retryable=False,
            )

        self._ensure_blob(
            db,
            digest=payload.content_sha256,
            size_bytes=payload.size_bytes,
            published_at=published_at,
        )
        self._insert_artifact(
            db,
            action=action,
            artifact_id=payload.artifact_id.root,
            digest=payload.content_sha256,
            kind="workspace_file",
            logical_name=payload.path,
            media_type=payload.media_type,
            published_at=published_at,
        )
        db.add(
            WorkspaceVersionRecord(
                action_id=action.action_id.root,
                session_id=action.session_id.root,
                path=payload.path,
                revision=revision,
                artifact_id=payload.artifact_id.root,
                previous_content_sha256=previous_sha256,
                content_sha256=payload.content_sha256,
                created_at=published_at,
            )
        )
        if manifest is None:
            db.add(
                WorkspaceFileRecord(
                    path=payload.path,
                    artifact_id=payload.artifact_id.root,
                    content_sha256=payload.content_sha256,
                    size_bytes=payload.size_bytes,
                    revision=revision,
                    updated_by_action_id=action.action_id.root,
                    updated_at=published_at,
                )
            )
        else:
            manifest.artifact_id = payload.artifact_id.root
            manifest.content_sha256 = payload.content_sha256
            manifest.size_bytes = payload.size_bytes
            manifest.revision = revision
            manifest.updated_by_action_id = action.action_id.root
            manifest.updated_at = published_at
        return {
            "kind": payload.kind,
            "artifact_id": str(payload.artifact_id),
            "path": payload.path,
            "content_sha256": payload.content_sha256,
            "previous_content_sha256": previous_sha256,
            "workspace_revision": revision,
            "workspace_total_bytes": next_total,
        }

    def _ensure_blob(
        self,
        db: Session,
        *,
        digest: str,
        size_bytes: int,
        published_at: datetime,
    ) -> None:
        storage_key = f"blobs/{digest[:2]}/{digest}"
        try:
            self._store.verify(
                storage_key=storage_key,
                digest=digest,
                size_bytes=size_bytes,
            )
        except ValueError as exc:
            raise ToolExecutionError(
                "artifact_blob_integrity_failed",
                "The prepared artifact blob failed integrity verification.",
                retryable=False,
            ) from exc
        existing = db.get(ArtifactBlobRecord, digest)
        if existing is not None:
            if existing.size_bytes != size_bytes or existing.storage_key != storage_key:
                raise ToolExecutionError(
                    "artifact_blob_metadata_conflict",
                    "Content-addressed artifact metadata is inconsistent.",
                    retryable=False,
                )
            return
        stored_bytes = db.scalar(select(func.coalesce(func.sum(ArtifactBlobRecord.size_bytes), 0)))
        if int(stored_bytes or 0) + size_bytes > self._max_artifact_store_bytes:
            raise ToolExecutionError(
                "artifact_store_quota_exceeded",
                "The content-addressed artifact store would exceed its policy quota.",
                retryable=False,
            )
        db.add(
            ArtifactBlobRecord(
                sha256=digest,
                size_bytes=size_bytes,
                storage_key=storage_key,
                created_at=published_at,
            )
        )
        db.flush()

    @staticmethod
    def _insert_artifact(
        db: Session,
        *,
        action: BoundAction,
        artifact_id: object,
        digest: str,
        kind: str,
        logical_name: str,
        media_type: str,
        published_at: datetime,
    ) -> None:
        existing = db.get(ArtifactRecord, artifact_id)
        if existing is not None:
            actual = (
                existing.session_id,
                existing.action_id,
                existing.blob_sha256,
                existing.kind,
                existing.logical_name,
                existing.media_type,
            )
            expected = (
                action.session_id.root,
                action.action_id.root,
                digest,
                kind,
                logical_name,
                media_type,
            )
            if actual != expected:
                raise ToolExecutionError(
                    "artifact_identity_conflict",
                    "The artifact identity is already bound to different content.",
                    retryable=False,
                )
            return
        db.add(
            ArtifactRecord(
                id=artifact_id,
                session_id=action.session_id.root,
                action_id=action.action_id.root,
                blob_sha256=digest,
                kind=kind,
                logical_name=logical_name,
                media_type=media_type,
                encoding="utf-8",
                safety_status="untrusted_model_output",
                created_at=published_at,
            )
        )
        db.flush()

    def _base_file_identity(self, path: str) -> tuple[str | None, int]:
        candidate = self._resolve_workspace_path(path)
        if candidate.is_symlink():
            raise ToolExecutionError(
                "workspace_path_collision",
                "A workspace write cannot replace or traverse a symbolic link.",
                retryable=False,
            )
        if candidate.is_dir():
            raise ToolExecutionError(
                "workspace_path_collision",
                "A workspace write cannot replace a directory.",
                retryable=False,
            )
        for parent in candidate.parents:
            if parent == self._workspace_root:
                break
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                raise ToolExecutionError(
                    "workspace_path_collision",
                    "A workspace write path collides with an existing workspace entry.",
                    retryable=False,
                )
        if not candidate.exists():
            return None, 0
        try:
            content = candidate.read_bytes()
        except OSError as exc:
            raise ToolExecutionError(
                "workspace_read_failed",
                "The current workspace file could not be inspected.",
                retryable=True,
            ) from exc
        return hashlib.sha256(content).hexdigest(), len(content)

    def _logical_workspace_size(self, manifest_rows: list[WorkspaceFileRecord]) -> int:
        overrides = {row.path for row in manifest_rows}
        total = sum(row.size_bytes for row in manifest_rows)
        try:
            for candidate in self._workspace_root.rglob("*"):
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                relative = candidate.relative_to(self._workspace_root).as_posix()
                if relative not in overrides:
                    total += candidate.stat().st_size
        except OSError as exc:
            raise ToolExecutionError(
                "workspace_quota_scan_failed",
                "The workspace could not be measured before publication.",
                retryable=True,
            ) from exc
        return total

    def _reject_path_collision(
        self,
        path: str,
        manifest_rows: list[WorkspaceFileRecord],
    ) -> None:
        requested = PurePosixPath(path)
        for row in manifest_rows:
            other = PurePosixPath(row.path)
            if other == requested:
                continue
            if other in requested.parents or requested in other.parents:
                raise ToolExecutionError(
                    "workspace_path_collision",
                    "The workspace path collides with an existing manifest entry.",
                    retryable=False,
                )

    def _resolve_workspace_path(self, path: str) -> Path:
        candidate = (self._workspace_root / path).resolve(strict=False)
        if not candidate.is_relative_to(self._workspace_root):
            raise ToolExecutionError(
                "workspace_path_outside_root",
                "The workspace path escapes its configured root.",
                retryable=False,
            )
        return candidate
