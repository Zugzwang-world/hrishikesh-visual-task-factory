"""Question programs v0.2: every answer is computed from a settled SceneSpec, never typed or guessed.

Implements the contract in `vtf.core.program` for the 12 families of spec/families.md:

- `run_program(name, params, scene, camera_id)` runs one program and returns a ProgramResult
  (answer, trace, G4 margins, tolerance, shortcut_conflict, difficulty), or raises ProgramReject
  when the question would be ambiguous (near ties, unnameable objects, no scale in frame, ...).
- `compile_questions(scene, camera_id, seed, max_questions)` chooses and phrases candidates.
- `make_twin(name, params, scene, camera_id, seed)` applies the family's twin rule (G6).
- `object_refs(scene)` lists every object's naming words for TaskPackage.scene_objects.

`python -m vtf.programs <scene.settled.json>` prints the compiled questions for one scene.
"""

from vtf.programs.compile import compile_questions
from vtf.programs.library import (
    NEAR_TIE_DEPTH_FRONT_M,
    NEAR_TIE_DEPTH_M,
    NEAR_TIE_GRIPPER_FRONT_M,
    NEAR_TIE_GRIPPER_M,
    PROGRAMS,
    PROGRAMS_VERSION,
    TABLE_CONTACT_TOL_M,
    canonical_name,
    run_program,
)
from vtf.programs.naming import object_refs
from vtf.programs.twins import make_twin

__all__ = [
    "NEAR_TIE_DEPTH_FRONT_M",
    "NEAR_TIE_DEPTH_M",
    "NEAR_TIE_GRIPPER_FRONT_M",
    "NEAR_TIE_GRIPPER_M",
    "PROGRAMS",
    "PROGRAMS_VERSION",
    "TABLE_CONTACT_TOL_M",
    "canonical_name",
    "compile_questions",
    "make_twin",
    "object_refs",
    "run_program",
]
