"""Blind (question-only) mode for the G5 vision-necessity baseline.

The same model and settings, but no image: the system prompt says so and asks for a best guess,
and the user turn carries only the question. Replies are filed under eval/<model>__blind/ and
cached under that key with their own prompt version, so they never mix with image runs.

Models appear here only as a filter (G5 evidence), never as the source of an answer.
"""

from pathlib import Path
from typing import Any

from vtf.core.program import AnswerType
from vtf.eval import prompt
from vtf.eval.adapters import Adapter
from vtf.eval.adapters.fake import FAKES
from vtf.eval.config import ModelSpec

BLIND_SUFFIX = "__blind"
BLIND_REVISION = "blind.1"
BLIND_INTRO = (
    "You will not see an image. The question is about a photo of a robot workcell: a table with "
    "everyday objects, seen by one of the robot's cameras. Answer from the question alone with "
    "your best guess. Always give an answer; never say that you cannot see the image."
)


def blind_key(model_key: str) -> str:
    return f"{model_key}{BLIND_SUFFIX}"


def blind_prompt_version() -> str:
    return f"{prompt.PROMPT_VERSION}+{BLIND_REVISION}"


def blind_system_prompt(answer_type: AnswerType) -> str:
    """The image prompt with its opening line swapped for BLIND_INTRO."""
    base = prompt.system_prompt(answer_type)
    intro = getattr(prompt, "_INTRO", "")
    if intro and intro in base:
        return base.replace(intro, BLIND_INTRO, 1)
    return f"{BLIND_INTRO}\n{base}"


def _blind_anthropic(spec: ModelSpec) -> Adapter:
    from vtf.eval.adapters.anthropic import AnthropicAdapter

    class BlindAnthropicAdapter(AnthropicAdapter):
        def request(
            self, image_path: Path, question: str, answer_type: AnswerType, structured: bool
        ) -> dict[str, Any]:
            kwargs = super().request(image_path, question, answer_type, structured)
            kwargs["system"] = blind_system_prompt(answer_type)
            for message in kwargs["messages"]:
                message["content"] = [b for b in message["content"] if b.get("type") != "image"]
            return kwargs

    return BlindAnthropicAdapter(spec.model_id, **spec.settings)


def make_blind_adapter(spec: ModelSpec) -> Adapter:
    """Question-only adapter for `spec`. Fake adapters never look at pixels, so they are reused."""
    if spec.provider == "fake":
        return FAKES[spec.model_id]()
    if spec.provider == "anthropic":
        return _blind_anthropic(spec)
    raise ValueError(f"{spec.key}: blind mode is not implemented for provider {spec.provider!r}")
