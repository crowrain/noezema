import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from packages.domain.models.enums import ActionStatus, IdempotencyClass, ToolName


class ActionProposal(BaseModel):
    turn_id: uuid.UUID
    model_run_id: uuid.UUID
    tool: ToolName
    arguments: dict = Field(default_factory=dict)
    public_rationale: str
    expected_information: str
    idempotency_class: IdempotencyClass
    host_id: uuid.UUID
    idempotency_key: uuid.UUID


class ActionRecord(BaseModel):
    host_id: uuid.UUID
    idempotency_key: uuid.UUID
    model_run_id: uuid.UUID
    turn_id: uuid.UUID
    tool: ToolName
    arguments: dict
    status: ActionStatus
    result: dict | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
