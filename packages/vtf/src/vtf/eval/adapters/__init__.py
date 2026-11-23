"""Thin model adapters (provider SDKs, OpenAI-compatible endpoints, Python callables)."""

from typing import TYPE_CHECKING

from vtf.eval.adapters.base import Adapter, AdapterResponse, FatalAdapterError
from vtf.eval.adapters.fake import FAKES

if TYPE_CHECKING:
    from vtf.eval.config import ModelSpec

__all__ = ["Adapter", "AdapterResponse", "FatalAdapterError", "make_adapter"]

# Settings from models.yaml that each provider's adapter accepts.
_ANTHROPIC_SETTINGS = {
    "max_tokens", "thinking", "effort", "structured_output", "timeout_s", "max_retries",
}  # fmt: skip


def make_adapter(spec: "ModelSpec") -> Adapter:
    if spec.provider == "fake":
        return FAKES[spec.model_id]()
    if spec.provider == "anthropic":
        from vtf.eval.adapters.anthropic import AnthropicAdapter

        unknown = set(spec.settings) - _ANTHROPIC_SETTINGS
        if unknown:
            raise ValueError(f"{spec.key}: unknown settings {sorted(unknown)}")
        return AnthropicAdapter(spec.model_id, **spec.settings)
    raise ValueError(f"{spec.key}: no adapter for provider {spec.provider!r} yet")
