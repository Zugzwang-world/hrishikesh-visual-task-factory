"""Eval condition "scale stated" (brief VTF-08): distance and free-space tasks made before
programs 0.3.0 never told the model what scale the image shows. This condition re-asks them with
one sentence in front of the question, the same sentence programs 0.3.0 now writes into new
questions (vtf.programs.templates.scale_sentence):

- grid scenes: "The table has a printed grid of 1 cm squares with bold lines every 10 cm."
- reference-object scenes (no grid; a procedural cube, block, cylinder or ball in view): its
  exact size by name, e.g. "The yellow block is exactly 10 by 10 cm and 4 cm tall." The grid
  sentence would be false there.

Everything else is the standard request: same system prompt, schema, image, settings and scorer.
Replies are cached under their own prompt version (PROMPT_VERSION + "+scale.1"), so they never mix
with the original answers, and results are filed apart from frozen v1 (scripts/ask_sweep.py
--condition scale writes <run>/sweep_scale/).
"""

from collections.abc import Callable

from vtf.core.scene import SceneSpec
from vtf.core.task import TaskPackage
from vtf.eval.prompt import PROMPT_VERSION
from vtf.programs.templates import scale_sentence, with_scale

SCALE_REVISION = "scale.1"
SCALE_FAMILIES = ("distance", "free_space")


def scale_prompt_version() -> str:
    return f"{PROMPT_VERSION}+{SCALE_REVISION}"


def task_scale(task: TaskPackage) -> dict | None:
    """The scale_in_frame step of the task's G1 trace (the program's own record of the scale)."""
    g1 = task.gates.get("G1")
    trace = (g1.evidence or {}).get("trace", []) if g1 is not None else []
    for step in trace:
        if isinstance(step, dict) and step.get("step") == "scale_in_frame":
            return step
    return None


def task_scale_sentence(task: TaskPackage, scene: Callable[[str], SceneSpec]) -> str | None:
    """The sentence for one task; `scene(scene_id)` gives its scene file (for the reference
    object's exact size). Names come from task.scene_objects (full names, colour included)."""
    if task.family not in SCALE_FAMILIES:
        return None
    ref = task_scale(task)
    if ref is None:
        return None
    if ref.get("kind") != "reference":
        return scale_sentence(ref)
    oid = ref.get("object_id")
    names = {o.id: o.name for o in task.scene_objects}
    obj = scene(task.scene_id).object(oid)
    return scale_sentence(ref, names.get(oid) or obj.name, obj.shape, tuple(obj.dimensions))


def stated_question(task: TaskPackage, scene: Callable[[str], SceneSpec]) -> str:
    """The question with the scale sentence in front (unchanged for other families, or when the
    question already states it)."""
    return with_scale(task.question, task_scale_sentence(task, scene))
