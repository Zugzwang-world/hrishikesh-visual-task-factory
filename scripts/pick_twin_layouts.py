"""Pick the physical-twin layouts to build (brief VTF-07 step 0): cover the most question
families with the fewest objects to buy.

Reads the twin run's scene files (no Blender). Per buildable scene it estimates which of the
recipe's families the layout can produce: count, distance, front_to_back, relation, support and
free_space need only three objects on the mat; count_attribute needs a colour or category shared
by two objects; containment needs an item in the bowl; occlusion needs an object 10 to 90 % hidden
in a camera view, measured with the exact mesh ray caster (vtf.core.raycast). Then it searches
for the set of `--k` scenes that maximises family coverage (each feature family in at least
`--per-family` layouts, and `--min-four` layouts with four objects so count answers vary),
then minimises pieces to buy (the most copies of each asset any chosen
layout needs), then objects to place.

  uv run python scripts/pick_twin_layouts.py --run out/twins --k 12 --out docs/twins/selection.json
"""

import argparse
import itertools
import json
from collections import Counter
from pathlib import Path

import numpy as np

from vtf.core.layout import RunLayout
from vtf.core.raycast import raycast
from vtf.core.rng import make_rng
from vtf.twin.layouts import load_layout_scene, shortfall
from vtf.twin.shopping import build_shopping_list

ALWAYS = ("count", "distance", "front_to_back", "relation", "support", "free_space")
FEATURES = ("count_attribute", "containment", "occlusion")


def features(scene) -> dict:
    fam = set(ALWAYS)
    colours = Counter(o.color_name for o in scene.objects if o.color_name)
    cats = Counter(o.category for o in scene.objects)
    if max(colours.values(), default=0) >= 2 or max(cats.values(), default=0) >= 2:
        fam.add("count_attribute")
    if any(o.placement.kind == "in" for o in scene.objects):
        fam.add("containment")
    occluded = {}
    for cam in scene.cameras:
        rc = raycast(scene, cam.id)
        for o in scene.objects:
            amodal = int(rc.amodal[o.pass_index].sum())
            if amodal == 0:
                continue
            visible = int((rc.index == o.pass_index).sum()) / amodal
            if 0.1 <= visible <= 0.9:
                occluded.setdefault(cam.id, []).append([o.name, round(visible, 3)])
    if occluded:
        fam.add("occlusion")
    return {"families": sorted(fam), "occluded": occluded}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="out/twins")
    ap.add_argument("--k", type=int, default=12)
    ap.add_argument("--pool", type=int, default=30, help="first N buildable scenes (the sheets)")
    ap.add_argument("--per-family", type=int, default=4)
    ap.add_argument("--min-four", type=int, default=3, help="layouts with 4 objects (count)")
    ap.add_argument("--out", default="docs/twins/selection.json")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    run = RunLayout(Path(args.run))
    quantities = {it.asset_id: it.quantity for it in build_shopping_list()}
    pool = {}
    for sid in run.scene_ids():
        if len(pool) >= args.pool:
            break
        scene, _ = load_layout_scene(run, sid)
        if shortfall(scene, quantities):
            continue
        info = features(scene)
        info["objects"] = len(scene.objects)
        info["assets"] = dict(Counter(o.shape for o in scene.objects))
        pool[sid] = info
        print(
            sid,
            info["objects"],
            info["families"][6:] if len(info["families"]) > 6 else "",
            info["occluded"] or "",
        )

    ids = sorted(pool)

    def score(sel) -> tuple:
        fam = Counter(f for s in sel for f in pool[s]["families"])
        # count answers must vary: at least `--min-four` layouts with four objects
        four = sum(pool[s]["objects"] >= 4 for s in sel)
        cover = (
            sum(min(fam[f], args.per_family) for f in FEATURES)
            + len(fam)
            + min(four, args.min_four)
        )
        need: Counter = Counter()
        for s in sel:
            for a, n in pool[s]["assets"].items():
                need[a] = max(need[a], n)
        pieces = sum(need.values())
        objects = sum(pool[s]["objects"] for s in sel)
        return (cover, -pieces, -len(need), -objects)

    rng = make_rng(args.seed)
    best = None
    for restart in range(200):
        sel = list(rng.choice(ids, size=args.k, replace=False)) if restart else []
        if not sel:  # greedy start
            while len(sel) < args.k:
                sel.append(max((s for s in ids if s not in sel), key=lambda s: score(sel + [s])))
        improved = True
        while improved:
            improved = False
            for i, cand in itertools.product(range(args.k), ids):
                if cand in sel:
                    continue
                trial = sel[:i] + [cand] + sel[i + 1 :]
                if score(trial) > score(sel):
                    sel, improved = trial, True
        if best is None or score(sel) > score(best):
            best = sel
    best = sorted(best)
    need: Counter = Counter()
    for s in best:
        for a, n in pool[s]["assets"].items():
            need[a] = max(need[a], n)
    fam = Counter(f for s in best for f in pool[s]["families"])
    result = {
        "run": str(args.run),
        "generate": "uv run vtf generate --recipe recipes/physical_twin.yaml --n 45 --seed 19 "
        "--out out/twins",
        "pool": ids,
        "k": args.k,
        "scene_ids": best,
        "family_layouts": dict(sorted(fam.items())),
        "pieces_to_buy": dict(sorted(need.items())),
        "pieces_total": int(sum(need.values())),
        "objects_total": int(sum(pool[s]["objects"] for s in best)),
        "per_scene": {s: pool[s] for s in best},
        "full_list_pieces": int(sum(quantities.values())),
    }
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ("per_scene", "pool")}, indent=1))


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
