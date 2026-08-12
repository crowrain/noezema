"""Immutable content-addressed storage used by trusted host components."""

from packages.artifacts.store import ContentAddressedArtifactStore, StagedBlob
from packages.artifacts.workspace import WorkspaceSnapshotFactory

__all__ = ["ContentAddressedArtifactStore", "StagedBlob", "WorkspaceSnapshotFactory"]
