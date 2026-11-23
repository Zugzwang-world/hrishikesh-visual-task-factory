"""Print compile_questions for one settled scene file, every camera unless --camera is given.

uv run python -m vtf.programs <run>/scenes/<scene_id>/render/scene.settled.json --seed 0
uv run python -m vtf.programs scene.settled.json --twins --trace
"""

import argparse
import json
from pathlib import Path

from vtf.core.io import read_scene
from vtf.programs.compile import compile_questions
from vtf.programs.twins import make_twin


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m vtf.programs", description=__doc__)
    parser.add_argument("scene", type=Path, help="A settled scene file (scene.settled.json).")
    parser.add_argument("--camera", default=None, help="Camera id (default: every camera).")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-questions", type=int, default=5)
    parser.add_argument("--twins", action="store_true", help="Also apply each twin rule.")
    parser.add_argument("--trace", action="store_true", help="Print each program trace.")
    args = parser.parse_args()

    scene = read_scene(args.scene)
    cameras = [args.camera] if args.camera else [c.id for c in scene.cameras]
    for camera_id in cameras:
        questions = compile_questions(scene, camera_id, args.seed, args.max_questions)
        n_ok = sum(q.rejected is None for q in questions)
        rig = scene.camera(camera_id).rig
        print(f"{scene.meta.scene_id} camera={camera_id} ({rig}) seed={args.seed}: "
              f"{n_ok} accepted, {len(questions) - n_ok} rejected")  # fmt: skip
        for q in questions:
            if q.result is None:
                print(f"  REJ  [{q.template_id}] {q.question}  {q.rejected} "
                      f"{json.dumps(q.rejected_detail, default=str)[:160]}")  # fmt: skip
                continue
            r = q.result
            bits = [f"evidence={r.evidence_object_ids}"]
            if r.margin is not None:
                bits.append(f"margin={r.margin:.3f} m")
            if r.margin_px is not None:
                bits.append(f"margin_px={r.margin_px:.1f}")
            if r.tolerance is not None:
                bits.append(f"tolerance={r.tolerance}")
            if r.shortcut_conflict is not None:
                bits.append(f"shortcut={r.shortcut_answer!r} conflict={r.shortcut_conflict}")
            print(f"  ok   [{q.template_id}] {q.question}  ->  {r.answer_text}")
            print(f"         {'  '.join(bits)}  trace={len(r.trace)} steps")
            if args.trace:
                for step in r.trace:
                    print(f"           {json.dumps(step, default=str)}")
            if args.twins:
                twin = make_twin(q.program.name, q.program.params, scene, camera_id, args.seed)
                if twin is None:
                    print("         twin: none found")
                else:
                    what = "remove" if twin.remove else f"move to {list(twin.new_pose.position)}"
                    print(f"         twin: {twin.object_id} {what} ({twin.reason}) -> "
                          f"{twin.expected_answer!r}")  # fmt: skip


if __name__ == "__main__":
    main()
