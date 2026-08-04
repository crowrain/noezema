"""Classified failures exposed by the LLM gateway."""

from __future__ import annotations


class LLMGatewayError(RuntimeError):
    """Base class for failures safe to classify at the orchestration boundary."""


class TransientBackendError(LLMGatewayError):
    """A transport or backend failure that may be retried before a result exists."""


class RetryExhaustedError(TransientBackendError):
    def __init__(self, attempts: int) -> None:
        self.attempts = attempts
        super().__init__(f"local LLM remained unavailable after {attempts} attempts")


class PermanentBackendError(LLMGatewayError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"local LLM rejected the request with HTTP {status_code}")


class BackendProtocolError(LLMGatewayError):
    """The endpoint returned a response outside the supported compatibility contract."""


class InvalidModelOutputError(LLMGatewayError):
    """The model returned content that does not validate as a DecisionEnvelope."""


class IncompleteModelOutputError(LLMGatewayError):
    def __init__(self, finish_reason: str) -> None:
        self.finish_reason = finish_reason
        super().__init__(f"model output is incomplete: finish_reason={finish_reason}")
