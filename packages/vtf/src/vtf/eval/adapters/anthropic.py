"""Anthropic Messages API adapter, on the official SDK.

- Credentials: ANTHROPIC_API_KEY only (the runner loads the repo-root .env); no other fallback.
- API version: the SDK's default (anthropic 2.x sends anthropic-version 2026-10-21).
- One user turn: the image (base64 PNG, native resolution) first, then the question text.
- No temperature, top_p or top_k: the 2026-10-21 API rejects them on every model.
- Reasoning: thinking and effort come from configs/models.yaml and are recorded in summary.json.
- JSON via structured outputs (output_config.format). If a request with the schema is rejected
  (400), the adapter retries it once without the schema, stays in plain-text mode for the rest of
  the run, and the scorer extracts {"answer": ...} from the text.
- Retries: the SDK retries 408, 409, 429, 5xx (529 overloaded included) and connection errors with
  exponential backoff, honouring retry-after; max_retries and the timeout come from the config.
- No server-side refusal fallback: a refusal is the evaluated model's answer, so it is scored
  (as unparseable) rather than silently answered by another model.
"""

import base64
import os
import threading
import time
from pathlib import Path
from typing import Any

import anthropic

from vtf.core.program import AnswerType
from vtf.eval.adapters.base import Adapter, AdapterResponse, FatalAdapterError
from vtf.eval.prompt import response_schema, system_prompt, user_text

_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
_FATAL = (
    anthropic.AuthenticationError,
    anthropic.PermissionDeniedError,
    anthropic.NotFoundError,
    anthropic.CredentialsError,
)


def _describe(exc: Exception) -> str:
    if isinstance(exc, anthropic.APIStatusError):
        return f"HTTP {exc.status_code} {exc.type or ''}: {exc.api_message or exc}".strip()
    return f"{type(exc).__name__}: {exc}"


class AnthropicAdapter(Adapter):
    provider = "anthropic"

    def __init__(
        self,
        model_id: str,
        *,
        max_tokens: int = 16000,
        thinking: str | None = None,
        effort: str | None = None,
        structured_output: bool = True,
        timeout_s: float = 120.0,
        max_retries: int = 6,
        client: Any = None,
    ) -> None:
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.thinking = thinking
        self.effort = effort
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.structured_output = structured_output
        self.structured_error: str | None = None
        self._lock = threading.Lock()
        if client is None:
            # Only the project key: without it the SDK would fall back to other credentials on
            # this machine (an `ant` login profile, say) and bill a different account.
            api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                raise FatalAdapterError(
                    "ANTHROPIC_API_KEY is not set: add it to the repo-root .env"
                )
            client = anthropic.Anthropic(
                api_key=api_key, timeout=timeout_s, max_retries=max_retries
            )
        self.client = client
        self.api_version = getattr(client, "default_headers", {}).get("anthropic-version")

    @property
    def settings(self) -> dict[str, Any]:
        return {
            "sdk": f"anthropic {anthropic.__version__}",
            "max_tokens": self.max_tokens,
            "thinking": self.thinking or "model default",
            "effort": self.effort or "model default",
            "sampling": "API defaults (temperature, top_p, top_k not sent)",
            "structured_output": self.structured_output,
            "structured_output_error": self.structured_error,
            "timeout_s": self.timeout_s,
            "max_retries": self.max_retries,
        }

    def request(
        self, image_path: Path, question: str, answer_type: AnswerType, structured: bool
    ) -> dict[str, Any]:
        """The keyword arguments for messages.create (public so tests can inspect them)."""
        image_path = Path(image_path)
        image = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": _MEDIA_TYPES.get(image_path.suffix.lower(), "image/png"),
                "data": base64.standard_b64encode(image_path.read_bytes()).decode("ascii"),
            },
        }
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self.max_tokens,
            "system": system_prompt(answer_type),
            "messages": [
                {"role": "user", "content": [image, {"type": "text", "text": user_text(question)}]}
            ],
        }
        if self.thinking:
            kwargs["thinking"] = {"type": self.thinking}
        output_config: dict[str, Any] = {}
        if self.effort:
            output_config["effort"] = self.effort
        if structured:
            output_config["format"] = {
                "type": "json_schema",
                "schema": response_schema(answer_type),
            }
        if output_config:
            kwargs["output_config"] = output_config
        return kwargs

    def _send(self, kwargs: dict[str, Any]) -> Any:
        try:
            return self.client.messages.create(**kwargs)
        except _FATAL as exc:
            raise FatalAdapterError(_describe(exc)) from exc

    def answer(self, image_path: Path, question: str, answer_type: AnswerType) -> AdapterResponse:
        structured = self.structured_output
        start = time.perf_counter()
        try:
            try:
                message = self._send(self.request(image_path, question, answer_type, structured))
            except anthropic.BadRequestError as exc:
                if not structured:
                    raise
                message = self._send(self.request(image_path, question, answer_type, False))
                structured = False
                with self._lock:  # the schema was the problem: plain text from now on
                    self.structured_output = False
                    self.structured_error = _describe(exc)
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            return AdapterResponse(
                raw_text="", latency_s=round(time.perf_counter() - start, 3), error=_describe(exc)
            )
        latency = round(time.perf_counter() - start, 3)
        text = "".join(block.text for block in message.content if block.type == "text")
        usage = message.usage
        return AdapterResponse(
            raw_text=text,
            latency_s=latency,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            stop_reason=message.stop_reason,
            meta={
                "output_mode": "json_schema" if structured else "text",
                "served_model": message.model,
                "request_id": getattr(message, "_request_id", None),
            },
        )
