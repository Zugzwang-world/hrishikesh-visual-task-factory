"""The adapter interface: one image and one question in, the model's raw reply out.

Adapters never parse or score. The runner caches what an adapter returns and the scorer turns the
raw text into an answer, so every provider is scored by the same code.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vtf.core.program import AnswerType


@dataclass(frozen=True)
class AdapterResponse:
    raw_text: str
    latency_s: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    """Set when no answer came back (API or network failure after retries). Never cached."""
    stop_reason: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    """Provider details worth keeping, e.g. output_mode, request_id, served model."""


class FatalAdapterError(RuntimeError):
    """Every call would fail the same way (bad credentials, unknown model): stop the run."""


class Adapter(ABC):
    provider: str = ""
    model_id: str = ""
    api_version: str | None = None

    @property
    def settings(self) -> dict[str, Any]:
        """Request settings recorded in summary.json (reasoning effort, max tokens, ...)."""
        return {}

    @abstractmethod
    def answer(self, image_path: Path, question: str, answer_type: AnswerType) -> AdapterResponse:
        """Ask the model one question about one image. Must be safe to call from worker threads."""
