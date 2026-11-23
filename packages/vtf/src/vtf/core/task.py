"""TaskPackage: one packaged task, written to <run>/tasks/<task_id>/task.json next to image.png.

Also EvalRecord: one model answer to one task, written by the eval harness to
<run>/eval/<model>/results.jsonl.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vtf.core.program import Answer, AnswerType, ProgramRef

TASK_VERSION = "0.2.0"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GateResult(BaseModel):
    """One gate's verdict. Serialised as {"pass": bool, "evidence": {...}}."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    passed: bool = Field(alias="pass")
    evidence: dict[str, Any] = Field(default_factory=dict)


class Versions(_Model):
    vtf: str
    spec: str = Field(description="SceneSpec version.")
    task: str = TASK_VERSION
    generator: str = Field(description="Sampler version.")
    programs: str = Field(description="Question-program version.")
    renderer: str = Field(description="Renderer and version, e.g. blender 5.3.0.")


class ObjectRef(_Model):
    """One scene object as the scorer sees it: the words that can name it."""

    id: str
    name: str = Field(description="Full name: [size] [colour] category.")
    category: str
    color_name: str | None = None
    size_word: str | None = None


class TaskPackage(_Model):
    task_version: str = TASK_VERSION
    task_id: str = Field(description="Content hash of the task's inputs and answer.")
    scene_id: str
    scene_hash: str = Field(description="sha256 of the settled scene file's canonical JSON.")
    camera_id: str
    rig: str = Field(default="front", description="front, overhead or gripper.")
    image: str = Field(default="image.png", description="Path relative to the task folder.")
    question: str
    question_alt: str | None = Field(
        default=None, description="The other phrasing (prompt-sensitivity subset)."
    )
    family: str
    template_id: str
    program: ProgramRef
    answer: Answer
    answer_type: AnswerType
    answer_text: str
    tolerance: float | None = Field(default=None, description="None means exact match.")
    shortcut_conflict: bool | None = Field(
        default=None,
        description="Spatial families: True when a 2D-proximity heuristic gives a different "
        "answer from the 3D truth. Also in `difficulty`. None for non-spatial families.",
    )
    shortcut_answer: Answer | None = None
    evidence_object_ids: list[str]
    scene_objects: list[ObjectRef] = Field(
        default_factory=list,
        description="Every object in the scene with its naming words, so the scorer can resolve "
        "a free-text answer to exactly one object (never shown to the model).",
    )
    gates: dict[str, GateResult] = Field(description="Gate id (G1, G3, G7, ...) -> result.")
    twin_of: str | None = Field(
        default=None, description="On a twin task: the task id of the original."
    )
    twin_id: str | None = Field(
        default=None, description="On an original with a G6 twin: the twin's task id."
    )
    difficulty: dict[str, int | float | str] = Field(default_factory=dict)
    seed: int = Field(description="Per-scene seed.")
    versions: Versions


class ManifestRow(_Model):
    """One line of <run>/manifest.jsonl: an accepted task."""

    task_id: str
    scene_id: str
    camera_id: str
    rig: str = "front"
    family: str
    answer_type: AnswerType
    shortcut_conflict: bool | None = None
    path: str = Field(description="tasks/<task_id>/task.json, relative to the run folder.")


class RejectedRow(_Model):
    """One line of <run>/rejected.jsonl."""

    scene_id: str
    camera_id: str | None = None
    family: str | None = None
    template_id: str | None = None
    question: str | None = None
    stage: str = Field(description="render, compile, G1, G3, G7, ...")
    reason: str
    detail: dict[str, Any] = Field(default_factory=dict)


class EvalRecord(_Model):
    """One model answer to one task (a line of <run>/eval/<model>/results.jsonl)."""

    task_id: str
    model: str
    prompt_version: str
    family: str
    camera_id: str | None = None
    rig: str | None = None
    shortcut_conflict: bool | None = None
    with_image: bool = Field(default=True, description="False for blind (question-only) runs.")
    answer_type: AnswerType
    truth: Answer
    raw_response: str
    parsed: Answer | None = None
    parse_ok: bool
    correct: bool
    cached: bool = False
    latency_s: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    thinking_tokens: int | None = Field(
        default=None, description="Output tokens spent on thinking (included in output_tokens)."
    )
    model_id: str | None = Field(default=None, description="The model ID sent in the request.")
    api_version: str | None = Field(default=None, description="anthropic-version (or similar).")
    sdk: str | None = Field(default=None, description="Client SDK and version.")
    effort: str | None = Field(default=None, description="Reasoning effort sent.")
    max_tokens: int | None = Field(default=None, description="max_tokens sent.")
    via: str | None = Field(
        default=None, description="standard (one Messages call) or batch (Message Batches API)."
    )
