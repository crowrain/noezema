"""Production composition root for the trusted autonomous runtime."""

from apps.runtime.config import RuntimeConfig
from apps.runtime.factory import RuntimeComponents, build_runtime

__all__ = ["RuntimeComponents", "RuntimeConfig", "build_runtime"]
