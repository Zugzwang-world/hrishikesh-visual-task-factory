"""TaskPackage: one packaged task, written to <run>/tasks/<task_id>/task.json next to image.png.

Also EvalRecord: one model answer to one task, written by the eval harness to
<run>/eval/<model>/results.jsonl.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vtf.core.program import Answer, AnswerType, ProgramRef

TASK_VERSION = "0.1.0"


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


class TaskPackage(_Model):
    task_version: str = TASK_VERSION
    task_id: str = Field(description="Content hash of the task's inputs and answer.")
    scene_id: str
    scene_hash: str = Field(description="sha256 of the settled scene file's canonical JSON.")
    camera_id: str
    image: str = Field(default="image.png", description="Path relative to the task folder.")
    question: str
    family: str
    template_id: str
    program: ProgramRef
    answer: Answer
    answer_type: AnswerType
    answer_text: str
    tolerance: float | None = Field(default=None, description="None means exact match.")
    evidence_object_ids: list[str]
    gates: dict[str, GateResult] = Field(description="Gate id (G1, G3, G7, ...) -> result.")
    twin_of: str | None = Field(default=None, description="Task id of the twin (null in v0).")
    difficulty: dict[str, int | float | str] = Field(default_factory=dict)
    seed: int = Field(description="Per-scene seed.")
    versions: Versions


class ManifestRow(_Model):
    """One line of <run>/manifest.jsonl: an accepted task."""

    task_id: str
    scene_id: str
    camera_id: str
    family: str
    answer_type: AnswerType
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
