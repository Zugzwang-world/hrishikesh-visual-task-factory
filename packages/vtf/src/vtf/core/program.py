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

Stage 3 adds (contract v0.2):

- `FAMILIES`: the 12 question families, their programs, answer type, whether they are spatial
  (spatial families carry the shortcut_conflict tag, decision 2 of brief 05) and which camera
  rigs may ask them. Programs register under these names; validate, G2, reports and the web
  viewer all key on them.
- `make_twin(name, params, scene, camera_id, seed) -> TwinEdit | None` (vtf.programs): the
  family's twin rule. It moves one evidence object so the answer should change (G6).
- ProgramResult.shortcut_conflict: True when the 2D-proximity heuristic answer (image-plane
  distance or vertical image position, defined per family in vtf.programs) differs from the 3D
  truth. None for non-spatial families.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from vtf.core.geometry import Pose

AnswerType = Literal["int", "object", "set", "bool", "number"]
Answer = int | bool | float | str | list[str]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProgramRef(_Model):
    name: str = Field(description="Registered program name, e.g. count_color.")
    params: dict[str, Any] = Field(default_factory=dict, description="JSON-safe parameters.")


class FamilySpec(_Model):
    family: str
    programs: tuple[str, ...]
    answer_type: AnswerType
    spatial: bool = Field(description="Carries the shortcut_conflict tag.")
    rigs: tuple[str, ...] = ("front", "overhead", "gripper")
    description: str = ""


FAMILIES: dict[str, FamilySpec] = {
    f.family: f
    for f in (
        FamilySpec(family="count", programs=("count_all", "count_category"), answer_type="int",
                   spatial=False, description="Count under clutter"),
        FamilySpec(family="count_attribute", programs=("count_color", "count_color_in"),
                   answer_type="int", spatial=False, description="Count by attribute"),
        FamilySpec(family="nearest_to_gripper", programs=("nearest_to_gripper",),
                   answer_type="object", spatial=True, description="Nearest to gripper"),
        FamilySpec(family="distance", programs=("distance_cm",), answer_type="number",
                   spatial=False, description="Distance with scale, in cm"),
        FamilySpec(family="front_to_back", programs=("front_to_back",), answer_type="object",
                   spatial=True, description="Front-to-back order"),
        FamilySpec(family="relation", programs=("relation",), answer_type="object",
                   spatial=True, description="Relation in a stated frame"),
        FamilySpec(family="support", programs=("support",), answer_type="object",
                   spatial=False, description="What an object rests on"),
        FamilySpec(family="containment", programs=("contents",), answer_type="set",
                   spatial=False, description="Which objects are inside a container"),
        FamilySpec(family="occlusion", programs=("occluded_by",), answer_type="object",
                   spatial=True, description="Which object is partly hidden by another"),
        FamilySpec(family="reachability", programs=("in_zone",), answer_type="set",
                   spatial=True, description="Which objects lie inside the reach zone"),
        FamilySpec(family="free_space", programs=("room_between",), answer_type="bool",
                   spatial=True, description="Is there room for an object between two others"),
        FamilySpec(family="gripper_view", programs=("gripper_center",), answer_type="object",
                   spatial=False, rigs=("gripper",),
                   description="Which object is at the centre of the gripper camera"),
    )
}  # fmt: skip

PROGRAM_FAMILY: dict[str, str] = {p: f.family for f in FAMILIES.values() for p in f.programs}


class ProgramResult(_Model):
    answer: Answer = Field(
        description="int: a count; object: unique name ('red mug', or 'table'); set: sorted "
        "names; bool; number: a value in the question's unit (cm)."
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
    margin_px: float | None = Field(
        default=None, description="Decision margin in pixels in this camera's image (G4)."
    )
    tolerance: float | None = Field(
        default=None, description="Scoring tolerance for number answers (same unit); else None."
    )
    shortcut_conflict: bool | None = Field(
        default=None,
        description="Spatial families: True when the 2D-proximity heuristic's answer differs "
        "from the 3D truth. None for non-spatial families.",
    )
    shortcut_answer: Answer | None = Field(
        default=None, description="What the 2D-proximity heuristic answers (spatial families)."
    )
    difficulty: dict[str, int | float | str | bool] = Field(
        default_factory=dict,
        description="Question-level difficulty (distractors, occlusion, margins, ...).",
    )


class CompiledQuestion(_Model):
    family: str = Field(description="A key of FAMILIES.")
    template_id: str = Field(description="Which phrasing was used, e.g. count_color.p1.")
    question: str
    question_alt: str | None = Field(
        default=None, description="The family's other phrasing of the same question."
    )
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


class TwinEdit(_Model):
    """A family's twin rule applied to one question (G6): move one evidence object.

    validate builds the twin scene from the settled scene: the moved object gets `new_pose`
    (spawned a little above its support), every other object keeps its settled pose, meta.settled
    is False and meta.twin_of names the original scene. The twin is re-rendered (and re-settled),
    the same program runs on it, and G6 requires the answer to differ from the original and every
    gate to pass on the twin.
    """

    object_id: str
    new_pose: Pose
    expected_answer: Answer | None = Field(
        default=None, description="The answer the rule expects on the twin (before re-settle)."
    )
    reason: str = Field(default="", description="What the move does, e.g. 'swap depth order'.")
