"""Print compile_questions for one settled scene file.

uv run python -m vtf.programs <run>/scenes/<scene_id>/render/scene.settled.json --seed 0
"""

import argparse
from pathlib import Path

from vtf.core.io import read_scene
from vtf.programs.compile import compile_questions


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m vtf.programs", description=__doc__)
    parser.add_argument("scene", type=Path, help="A settled scene file (scene.settled.json).")
    parser.add_argument("--camera", default=None, help="Camera id (default: the first camera).")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-questions", type=int, default=5)
    args = parser.parse_args()

    scene = read_scene(args.scene)
    camera_id = args.camera or scene.cameras[0].id
    questions = compile_questions(scene, camera_id, args.seed, args.max_questions)
    n_ok = sum(q.rejected is None for q in questions)
    print(f"{scene.meta.scene_id} camera={camera_id} seed={args.seed}: "
          f"{n_ok} accepted, {len(questions) - n_ok} rejected")  # fmt: skip
    for q in questions:
        params = q.program.params
        if q.result is not None:
            margin = "" if q.result.margin is None else f"  margin={q.result.margin:.3f} m"
            print(f"  ok   [{q.template_id}] {q.question}  ->  {q.result.answer_text}"
                  f"  evidence={q.result.evidence_object_ids}{margin}")  # fmt: skip
        else:
            print(f"  REJ  [{q.template_id}] {q.question}  params={params}  "
                  f"{q.rejected} {q.rejected_detail}")  # fmt: skip


if __name__ == "__main__":
    main()
