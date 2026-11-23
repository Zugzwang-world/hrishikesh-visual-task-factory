"""G1 Truth by execution: a typed program on the settled scene file gives the answer.

The compiled question already carries the program's result. G1 runs the program again from its
stored name and params on the settled scene file and requires the identical answer, then
records the program, its trace and the answer as evidence.
"""

from vtf.core.hashing import sha256_json
from vtf.core.program import CompiledQuestion, ProgramReject
from vtf.core.scene import SceneSpec
from vtf.core.task import GateResult


def check_g1(question: CompiledQuestion, settled: SceneSpec) -> GateResult:
    from vtf.programs import run_program

    if question.result is None:
        return GateResult(passed=False, evidence={"reason": "no_result"})
    try:
        rerun = run_program(
            question.program.name, question.program.params, settled, question.camera_id
        )
    except ProgramReject as exc:
        return GateResult(passed=False, evidence={"reason": f"rerun_rejected:{exc.reason}"})
    same = sha256_json(rerun.answer) == sha256_json(question.result.answer)
    evidence = {
        "program": question.program.name,
        "params": question.program.params,
        "answer": question.result.answer,
        "rerun_answer": rerun.answer,
        "rerun_matches": same,
        "settled": settled.meta.settled,
        "trace": question.result.trace,
    }
    if not same:
        evidence["reason"] = "rerun_mismatch"
    return GateResult(passed=same and settled.meta.settled, evidence=evidence)
