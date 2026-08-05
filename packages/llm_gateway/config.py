from os import environ

from pydantic import BaseModel, Field


class LLMGatewayConfig(BaseModel):
    base_url: str = Field(default="http://localhost:8080/v1")
    model: str = Field(default="qwen3.6-35b")
    max_tokens: int = Field(default=4096)
    temperature: float = Field(default=0.7)
    timeout: float = Field(default=120.0)
    embedding_model: str = Field(default="")
    tokenizer: str = Field(default="cl100k_base")

    @classmethod
    def from_env(cls) -> "LLMGatewayConfig":
        """Load config from environment variables."""
        return cls(
            base_url=environ.get("LLM_API_BASE", "http://localhost:8080/v1"),
            model=environ.get("LLM_MODEL", "qwen3.6-35b"),
            max_tokens=int(environ.get("LLM_MAX_TOKENS", "4096")),
            temperature=float(environ.get("LLM_TEMPERATURE", "0.7")),
            timeout=float(environ.get("LLM_TIMEOUT", "120")),
        )
