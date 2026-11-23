"""Question programs v0: every answer is computed from a settled SceneSpec, never typed or guessed.

Implements the contract in `vtf.core.program`:

- `run_program(name, params, scene, camera_id)` runs one program (count_all, count_color,
  nearest_to_gripper, front_to_back) and returns a ProgramResult, or raises ProgramReject when the
  question would be ambiguous (near ties, unclear table support, duplicate names).
- `compile_questions(scene, camera_id, seed, max_questions)` chooses and phrases candidates.

`python -m vtf.programs <scene.settled.json>` prints the compiled questions for one scene.
"""

from vtf.programs.compile import compile_questions
from vtf.programs.library import (
    NEAR_TIE_DEPTH_M,
    NEAR_TIE_GRIPPER_M,
    PROGRAMS,
    PROGRAMS_VERSION,
    TABLE_CONTACT_TOL_M,
    canonical_name,
    run_program,
)

__all__ = [
    "NEAR_TIE_DEPTH_M",
    "NEAR_TIE_GRIPPER_M",
    "PROGRAMS",
    "PROGRAMS_VERSION",
    "TABLE_CONTACT_TOL_M",
    "canonical_name",
    "compile_questions",
    "run_program",
]
