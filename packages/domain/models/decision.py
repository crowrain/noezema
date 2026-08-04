from pydantic import BaseModel, Field


class Decision(BaseModel):
    """One decision per model response."""
    kind: str
    tool: str | None = None
    arguments: dict | None = None
    reason: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.kind == "complete"


class ModelResponse(BaseModel):
    """Structured response from the LLM."""
    public_rationale: str
    decision: Decision
