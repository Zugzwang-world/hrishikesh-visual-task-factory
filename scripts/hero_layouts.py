"""Hero layouts (brief VTF-08, Phase 1): physical-twin layouts that show the 2D-shortcut trap on a
real photo from the front phone spot.

The 11 photo layouts hold about 3 objects each and give only 8 conflict tasks (1 on the front
spot), so a real photo where Claude takes the 2D shortcut is unlikely. Here:

  sample   sample layouts from recipes/hero_twin.yaml (5 to 7 of the 15 shopping-list assets on
           the mat) one seed at a time, skipping seeds whose objects do not fit; writes
           out/twins_hero/scenes/ and run.json (so G7 can re-sample any kept scene)
  screen   for each layout, the front-spot digital twin's question candidates in the families
           with a 2D shortcut (free_space, front_to_back, relation), run through the programs on
           the exact mesh ray cast (no Blender; poses as sampled, so this is an estimate); rank by
           the chance that the questions validate asks include a conflict, then by pieces to buy
           beyond the current shopping list -> out/twins_hero/screen.json
  digital  front-spot digital twins of the top layouts -> out/twins_hero_digital/scenes/hero_NNNN
           (render with `vtf render`, validate with `vtf validate --no-twins`)
  tasks    the conflict tasks of the rendered hero twins -> out/twins_hero_digital/conflict_ids.json
  pick     after Sonnet and Opus answered them (scripts/ask_sweep.py --name trap_hero): keep up to
           3 layouts where both models give the shortcut answer on at least one conflict question
           (fallback: one model); write the hero block of docs/twins/selection.json

Every step is a pure function of the recipe, the seed and the code.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from vtf.core.io import read_jsonl, read_scene, read_task, write_model
from vtf.core.layout import RunLayout, scene_id_for
from vtf.core.program import PROGRAM_FAMILY, ProgramReject
from vtf.core.rng import derive_seed, make_rng
from vtf.core.scene import RunInfo
from vtf.eval.runner import score_task
from vtf.programs.compile import _Proposer
from vtf.programs.library import run_program
from vtf.programs.view import hidden_evidence
from vtf.scene.generate import generator_version, sample_any
from vtf.scene.recipe import load_recipe
from vtf.scene.sampler_v1 import LayoutError
from vtf.twin.digital import spot, spot_camera, twin_scene

RECIPE = Path("recipes/hero_twin.yaml")
SAMPLED = Path("out/twins_hero")
DIGITAL = Path("out/twins_hero_digital")
SELECTION = Path("docs/twins/selection.json")
SEED = 808
FAMILIES = ("free_space", "front_to_back", "relation")
# Share of front-rig conflict tasks on frozen v1 where Sonnet and Opus (high) both gave the 2D
# shortcut answer, roughly (free_space 8 and 11 of 18, front_to_back 2 and 3 of 3, relation 4 and
# 2 of 37): the screen weights conflicts by how often they fool the models.
FOOL_WEIGHT = {"free_space": 0.5, "front_to_back": 0.7, "relation": 0.05}
ASKED_PER_FAMILY = 2  # compile_questions: at most 2 rounds, one accepted question per visit


def hero_id(scene_id: str) -> str:
    return "hero_" + scene_id.split("_")[-1]


def front_twin(scene, scene_id: str | None = None):
    return twin_scene(scene, [spot_camera(spot("front"), scene)], scene_id=scene_id)


# --- sample -----------------------------------------------------------------------------------


def cmd_sample(args: argparse.Namespace) -> None:
    rec = load_recipe(RECIPE)
    lay = RunLayout(SAMPLED)
    seeds: dict[str, int] = {}
    failed = 0
    for i in range(args.n):
        sid, seed = scene_id_for(i), derive_seed(args.seed, i)
        try:
            scene = sample_any(rec, sid, seed)
        except LayoutError:
            failed += 1
            continue
        write_model(lay.scene_file(sid), scene)
        seeds[sid] = seed
    info = RunInfo(recipe=str(RECIPE), recipe_hash=rec.recipe_hash, run_seed=args.seed,
                   n=len(seeds), generator_version=generator_version(rec), scenes=seeds)
    write_model(lay.run_file, info)
    counts = Counter(len(read_scene(lay.scene_file(s)).objects) for s in seeds)
    print(f"hero sample: {len(seeds)} layouts kept, {failed} seeds did not fit; objects "
          f"{dict(sorted(counts.items()))} -> {SAMPLED}")  # fmt: skip


# --- screen -----------------------------------------------------------------------------------


def screen_scene(scene) -> dict[str, Any]:
    twin = front_twin(scene)
    twin = twin.model_copy(update={"meta": twin.meta.model_copy(update={"settled": True})})
    prop = _Proposer(twin, "front", make_rng(0))
    fams: dict[str, dict[str, Any]] = {}
    for fam in FAMILIES:
        ok, conflicts = 0, []
        for prog, params, slots in getattr(prop, fam)():
            try:
                res = run_program(prog, params, twin, "front")
            except ProgramReject:
                continue
            if hidden_evidence(prop.view, res.evidence_object_ids, PROGRAM_FAMILY[prog], params):
                continue
            ok += 1
            if res.shortcut_conflict:
                conflicts.append({"params": params, "slots": slots, "answer": res.answer,
                                  "shortcut": res.shortcut_answer})  # fmt: skip
        fams[fam] = {"passing": ok, "conflicts": len(conflicts), "examples": conflicts[:3]}
    # chance that none of the (up to 2) asked questions per family is a conflict
    p_none = 1.0
    expected_fooled = 0.0
    for fam, f in fams.items():
        if f["passing"]:
            share = f["conflicts"] / f["passing"]
            k = min(ASKED_PER_FAMILY, f["passing"])
            p_none *= (1 - share) ** k
            expected_fooled += k * share * FOOL_WEIGHT[fam]
    return {"families": fams, "p_conflict_asked": round(1 - p_none, 3),
            "expected_fooled": round(expected_fooled, 3)}  # fmt: skip


def pieces_needed(scene, have: dict[str, int]) -> dict[str, int]:
    need = Counter(o.shape for o in scene.objects)
    return {a: n - have.get(a, 0) for a, n in sorted(need.items()) if n > have.get(a, 0)}


def cmd_screen(args: argparse.Namespace) -> None:
    lay = RunLayout(SAMPLED)
    have = json.loads(SELECTION.read_text())["pieces_to_buy"]
    rows = []
    for sid in lay.scene_ids():
        scene = read_scene(lay.scene_file(sid))
        row = {"scene_id": sid, "objects": len(scene.objects),
               "names": [o.name for o in scene.objects], **screen_scene(scene),
               "new_pieces": pieces_needed(scene, have)}  # fmt: skip
        row["n_new_pieces"] = sum(row["new_pieces"].values())
        rows.append(row)
        print(f"{sid}: expected fooled {row['expected_fooled']}, p(conflict asked) "
              f"{row['p_conflict_asked']}, new pieces {row['n_new_pieces']}", flush=True)
    rows.sort(key=lambda r: (-r["expected_fooled"], r["n_new_pieces"], r["scene_id"]))
    (SAMPLED / "screen.json").write_text(json.dumps(rows, indent=1) + "\n")
    print(f"hero screen: {len(rows)} layouts -> {SAMPLED / 'screen.json'}")


# --- digital ----------------------------------------------------------------------------------


def cmd_digital(args: argparse.Namespace) -> None:
    lay, dst = RunLayout(SAMPLED), RunLayout(DIGITAL)
    ids = args.ids or [r["scene_id"] for r in json.loads((SAMPLED / "screen.json").read_text())
                       if r["n_new_pieces"] <= args.max_new][: args.top]  # fmt: skip
    for sid in ids:
        scene = read_scene(lay.scene_file(sid))
        write_model(dst.scene_file(hero_id(sid)), front_twin(scene, hero_id(sid)))
    print(f"hero digital: {len(ids)} front-spot twins -> {dst.scenes_dir}: {ids}")


# --- tasks and pick ---------------------------------------------------------------------------


def conflict_rows(run: Path) -> list[dict]:
    return [r for r in read_jsonl(run / "manifest.jsonl") if r.get("shortcut_conflict")]


def cmd_tasks(args: argparse.Namespace) -> None:
    rows = conflict_rows(DIGITAL)
    ids = [r["task_id"] for r in rows]
    (DIGITAL / "conflict_ids.json").write_text(json.dumps({"n": len(ids), "task_ids": ids}) + "\n")
    print(f"hero tasks: {len(ids)} conflict tasks in "
          f"{len({r['scene_id'] for r in rows})} layouts: "
          f"{dict(Counter(r['family'] for r in rows))}")  # fmt: skip


def took_shortcut(task, raw: str) -> bool:
    """The reply is wrong and the scorer accepts it against the task's shortcut answer."""
    if task.shortcut_answer is None or score_task(task, raw).correct:
        return False
    return score_task(task.model_copy(update={"answer": task.shortcut_answer}), raw).correct


def shortcut_table(run: Path, name: str, models: list[str]) -> list[dict]:
    """Every conflict task of `run` with each model's reply and whether it took the shortcut."""
    out = []
    replies = {m: {r["task_id"]: r for r in read_jsonl(run / name / m / "results.jsonl")}
               for m in models}  # fmt: skip
    for row in conflict_rows(run):
        tid = row["task_id"]
        if not all(tid in replies[m] for m in models):
            continue
        task = read_task(run / row["path"])
        rec = {"task_id": tid, "scene_id": row["scene_id"], "camera_id": row["camera_id"],
               "family": row["family"], "question": task.question, "truth": task.answer,
               "shortcut_answer": task.shortcut_answer, "models": {}}  # fmt: skip
        for m in models:
            r = replies[m][tid]
            rec["models"][m] = {"parsed": r["parsed"], "correct": r["correct"],
                                "shortcut": took_shortcut(task, r["raw_response"])}  # fmt: skip
        rec["fooled_both"] = all(v["shortcut"] for v in rec["models"].values())
        rec["fooled_any"] = any(v["shortcut"] for v in rec["models"].values())
        out.append(rec)
    return out


def cmd_pick(args: argparse.Namespace) -> None:
    table = shortcut_table(DIGITAL, args.name, args.models)
    by_scene: dict[str, list[dict]] = {}
    for rec in table:
        by_scene.setdefault(rec["scene_id"], []).append(rec)
    screen = {hero_id(r["scene_id"]): r for r in json.loads((SAMPLED / "screen.json").read_text())}

    def rank(sid: str) -> tuple:
        recs = by_scene[sid]
        return (-sum(r["fooled_both"] for r in recs), -sum(r["fooled_any"] for r in recs),
                screen[sid]["n_new_pieces"], sid)  # fmt: skip

    both = sorted((s for s, recs in by_scene.items() if any(r["fooled_both"] for r in recs)),
                  key=rank)  # fmt: skip
    kept = both[:3]
    fallback = None
    if not kept:
        one = sorted((s for s, recs in by_scene.items() if any(r["fooled_any"] for r in recs)),
                     key=rank)  # fmt: skip
        kept = one[:1]
        fallback = "no layout fooled both models; kept the best layout that fooled one"
    sel = json.loads(SELECTION.read_text())
    sel["hero"] = {
        "brief": "VTF-08 Phase 1: hero layouts for the front phone spot, shot first",
        "recipe": str(RECIPE),
        "sampled_run": str(SAMPLED),
        "twins_run": str(DIGITAL),
        "spots": ["front"],
        "layouts": [
            {"label": f"H{i + 1}", "scene_id": sid, "sampled_scene_id": screen[sid]["scene_id"],
             "fooled_both": [r["task_id"] for r in by_scene[sid] if r["fooled_both"]],
             "fooled_any": [r["task_id"] for r in by_scene[sid] if r["fooled_any"]]}
            for i, sid in enumerate(kept)
        ],  # fmt: skip
        "film": "H1" if kept else None,
        "fallback": fallback,
        # layouts are shot one at a time, so a piece two hero layouts need is bought once
        "new_pieces": {a: max(screen[s]["new_pieces"].get(a, 0) for s in kept)
                       for a in sorted({a for s in kept for a in screen[s]["new_pieces"]})},
    }
    SELECTION.write_text(json.dumps(sel, indent=2) + "\n")
    (DIGITAL / "trap_table.json").write_text(json.dumps(table, indent=1) + "\n")
    print(json.dumps(sel["hero"], indent=1))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command", choices=["sample", "screen", "digital", "tasks", "pick"])
    p.add_argument("--n", type=int, default=400)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--top", type=int, default=8)
    p.add_argument("--max-new", type=int, default=3, help="most new pieces a hero layout may need")
    p.add_argument("--ids", nargs="*", default=None)
    p.add_argument("--name", default="trap_hero")
    p.add_argument("--models", nargs="+", default=["claude-sonnet-5-5", "claude-opus-5-5"])
    args = p.parse_args(argv)
    {"sample": cmd_sample, "screen": cmd_screen, "digital": cmd_digital, "tasks": cmd_tasks,
     "pick": cmd_pick}[args.command](args)  # fmt: skip


if __name__ == "__main__":
    sys.exit(main())
