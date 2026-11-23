"""The contract between question programs (vtf.programs) and the validate step.

`vtf.programs` exposes two functions:

    run_program(name: str, params: dict, scene: SceneSpec, camera_id: str) -> ProgramResult
        Run one program on a *settled* scene. Pure and deterministic. Raises ProgramReject when
        the question is ill-posed for this scene (for example a near tie).

    compile_questions(scene: SceneSpec, camera_id: str, seed: int, max_questions: int = 5)
        -> list[CompiledQuestion]
        Choose and phrase candidate questions for one scene and camera, seeded. Rejected
        candidates are returned too, with `rejected` set, so they reach rejected.jsonl.

G1 re-runs `run_program` from the stored program name and params and checks the answer matches.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AnswerType = Literal["int", "object", "set", "bool"]
Answer = int | bool | str | list[str]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProgramRef(_Model):
    name: str = Field(description="Registered program name, e.g. count_color.")
    params: dict[str, Any] = Field(default_factory=dict, description="JSON-safe parameters.")


class ProgramResult(_Model):
    answer: Answer = Field(
        description="int: a count; object: canonical name ('red box'); set: sorted names; bool."
    )
    answer_type: AnswerType
    answer_text: str = Field(description="Human-readable answer, e.g. '3' or 'red box'.")
    evidence_object_ids: list[str] = Field(
        description="Every object the answer depends on (for counts: every matching object)."
    )
    trace: list[dict[str, Any]] = Field(
        default_factory=list, description="Step-by-step values the program computed (G1 evidence)."
    )
    margin: float | None = Field(
        default=None, description="Decision margin in metres (gap to the runner-up), if any."
    )


class CompiledQuestion(_Model):
    family: str = Field(description="count, nearest_to_gripper or front_to_back in v0.")
    template_id: str = Field(description="Which phrasing was used, e.g. count_color.p1.")
    question: str
    camera_id: str
    program: ProgramRef
    result: ProgramResult | None = None
    rejected: str | None = Field(default=None, description="Reason code when rejected.")
    rejected_detail: dict[str, Any] = Field(default_factory=dict)


class ProgramReject(Exception):  # noqa: N818 - a domain outcome, not an error
    """Raised by a program when a question would be ambiguous for this scene."""

    def __init__(self, reason: str, **detail: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
