"""Pydantic schemas for LLM responses in the curator phase."""

from pydantic import BaseModel, Field


class ClaimProposal(BaseModel):
    """A single claim proposed by the curator LLM."""
    statement: str = Field(description="The claim statement — factual, testable, unambiguous")
    claim_type: str = Field(
        description="One of: empirical_conjecture, contextual_fact, model_hypothesis, operational_observation, meta_claim"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence 0.0-1.0")
    rationale: str = Field(description="Why this claim follows from the evidence")


class CuratorResponse(BaseModel):
    """LLM response for curator: generate claims from evidence."""
    claims: list[ClaimProposal] = Field(description="List of claims derived from evidence")
    summary: str = Field(description="Brief summary of findings (2-3 sentences)")
    gaps: list[str] = Field(default=[], description="What is still unknown or needs more evidence")
