"""G7 Determinism: same seed, same task.

At validate time G7 records the hashes that `vtf verify` later reproduces: the sampled scene file
and settled scene file (bytes), the answer (canonical JSON), and the decoded index and depth
arrays. It also checks what can be checked without re-rendering: the render was made from this
exact scene file, and, when the recipe is available, re-sampling the scene from its seed gives
the same bytes.
"""

from vtf.core.hashing import sha256_json
from vtf.core.task import GateResult


def check_g7(
    answer: object,
    scene_hashes: dict,
) -> GateResult:
    """`scene_hashes` is computed once per scene by `scene_determinism` in the pipeline."""
    evidence = dict(scene_hashes)
    evidence["answer_sha256"] = sha256_json(answer)
    problems = [
        k
        for k in ("render_matches_scene_file", "scene_regenerates_identically")
        if evidence.get(k) is False
    ]
    if problems:
        evidence["reason"] = "determinism_check_failed"
        evidence["failing"] = problems
    return GateResult(passed=not problems, evidence=evidence)
