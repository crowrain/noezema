import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from packages.domain.models.enums import SessionState


class Session(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    state: SessionState = SessionState.CREATED
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    last_progress_at: datetime | None = None
    phase_deadline: datetime | None = None
    config_snapshot_id: uuid.UUID | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    stop_requested_at: datetime | None = None
    abort_requested_at: datetime | None = None
    question_id: uuid.UUID | None = None
    workspace_manifest_id: uuid.UUID | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state.is_terminal
