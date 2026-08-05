import json
import uuid
from datetime import datetime

import httpx
from pydantic import BaseModel

from packages.llm_gateway.config import LLMGatewayConfig


class ModelRunRecord(BaseModel):
    run_id: uuid.UUID
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    started_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


class TransientError(Exception):
    """Retryable error from LLM."""
    pass


class SchemaValidationError(Exception):
    """LLM response did not match expected schema."""
    pass


class LLMMiddleware:
    """OpenAI-compatible gateway with schema validation."""

    def __init__(self, config: LLMGatewayConfig):
        self.config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout,
        )

    async def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[BaseModel],
        run_id: uuid.UUID,
    ) -> tuple[BaseModel, ModelRunRecord]:
        start = datetime.now()
        record = ModelRunRecord(run_id=run_id, model=self.config.model, started_at=start)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        payload = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "response_format": {"type": "json_object"},
        }

        response = await self._client.post("/chat/completions", json=payload)

        if response.status_code in (502, 503, 504):
            record.error = f"http_{response.status_code}"
            record.completed_at = datetime.now()
            record.latency_ms = (record.completed_at - start).total_seconds() * 1000
            raise TransientError(f"HTTP {response.status_code}")

        response.raise_for_status()
        data = response.json()
        raw_text = data["choices"][0]["message"]["content"]

        try:
            parsed = json.loads(raw_text)
            result = response_schema.model_validate(parsed)
        except Exception as e:
            record.error = f"schema_validation: {e}"
            record.completed_at = datetime.now()
            record.latency_ms = (record.completed_at - start).total_seconds() * 1000
            raise SchemaValidationError(str(e)) from e

        usage = data.get("usage", {})
        record.input_tokens = usage.get("prompt_tokens", 0)
        record.output_tokens = usage.get("completion_tokens", 0)
        record.completed_at = datetime.now()
        record.latency_ms = (record.completed_at - start).total_seconds() * 1000

        return result, record

    async def close(self):
        await self._client.aclose()
