"""Content-addressed artifact store + workspace freeze (T2.11, T2.12)."""

from packages.artifacts.store import ArtifactStore, FilesystemArtifactStore
from packages.artifacts.workspace import freeze_workspace

__all__ = ["ArtifactStore", "FilesystemArtifactStore", "freeze_workspace"]
