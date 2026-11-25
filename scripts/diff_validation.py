"""Compare the accepted base tasks of two validations of the same scenes (brief 06, phase E).

For each scene: task ids only in A, only in B, and shared ids whose answer differs. G6 twin
tasks (twin_of set) are left out: the check run is validated without twins.

  uv run python scripts/diff_validation.py <run A> <run B> --out docs/reports/v1/v1_1_check_diff
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def base_tasks(run: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = defaultdict(dict)
    for path in sorted((run / "tasks").glob("*/task.json")):
        t = json.loads(path.read_text())
        if t.get("twin_of"):
            continue
        out[t["scene_id"]][t["task_id"]] = {
            "family": t["family"],
            "camera_id": t["camera_id"],
            "answer": t["answer"],
            "shortcut_conflict": t.get("shortcut_conflict"),
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("a", type=Path)
    ap.add_argument("b", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    a, b = base_tasks(args.a), base_tasks(args.b)
    scenes = sorted(set(a) | set(b))
    per_scene = {}
    same = 0
    for s in scenes:
        ia, ib = set(a.get(s, {})), set(b.get(s, {}))
        changed = sorted(t for t in ia & ib if a[s][t]["answer"] != b[s][t]["answer"])
        if ia == ib and not changed:
            same += 1
            continue
        per_scene[s] = {
            "only_in_a": sorted(ia - ib),
            "only_in_b": sorted(ib - ia),
            "answer_changed": changed,
            "new_tasks": [{"task_id": t, **b[s][t]} for t in sorted(ib - ia)],
        }
    result = {
        "a": f"out/{args.a.resolve().name}",
        "b": f"out/{args.b.resolve().name}",
        "scenes_with_tasks": {"a": len(a), "b": len(b)},
        "base_tasks": {"a": sum(len(v) for v in a.values()), "b": sum(len(v) for v in b.values())},
        "scenes_identical": same,
        "scenes_different": sorted(per_scene),
        "per_scene": per_scene,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "per_scene"}, indent=2))
    for s, d in per_scene.items():
        print(s, {k: len(v) for k, v in d.items() if k != "new_tasks"})


if __name__ == "__main__":
    main()
