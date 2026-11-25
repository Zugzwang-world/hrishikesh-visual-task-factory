# ruff: noqa: E501 (the data card template keeps one paragraph per line)
"""Package frozen dataset v1 for Hugging Face (P26) as hard links, so it costs no disk.

    uv run python scripts/hf_release.py --run out/v1 --out out/hf_release
    hf upload-large-folder Hrishi1802/hrishikesh-visual-task-factory out/hf_release \\
        --repo-type dataset

The release folder is a run folder (vtf.core.layout), so after `hf download` a judge can point
any vtf command at it, e.g. `uv run vtf verify <folder> --sample 3 --device cpu`. It holds:

- the 3,181 frozen tasks (tasks/<task_id>/task.json + image.png) and manifest.jsonl;
- every scene those tasks come from: 261 base scenes and 484 G6 twin scenes, each with its scene
  file, settled scene, render manifest and per camera the beauty image, object index, depth
  and amodal masks, plus the per-scene gate results (validate/);
- run.json, rejected.jsonl, validate_report.json, FROZEN.json, the G5 baselines, every model's
  answers (eval/ and sweep/), the dataset report, and tasks/metadata.jsonl for the Hub viewer;
- README.md, the data card.

It refuses to run unless the four frozen sha256 in FROZEN.json still match. Files are hard
links (os.link) into the frozen run; nothing in the run is written. Render logs are left out
(they hold machine paths).
"""

import argparse
import hashlib
import json
import os
import shutil
from collections import Counter
from pathlib import Path

REPO = "Hrishi1802/hrishikesh-visual-task-factory"
GITHUB = "https://github.com/Zugzwang-world/hrishikesh-visual-task-factory"
EXPLORER = "https://vtf-task-explorer.vercel.app"
SKIP_NAMES = {"render.log", ".DS_Store"}
SKIP_DIRS = {".tmp"}
TOP_FILES = ["run.json", "manifest.jsonl", "rejected.jsonl", "validate_report.json", "FROZEN.json"]
TOP_DIRS = ["baselines", "eval", "report"]
SWEEP_FILES = ["costs.json"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def link(src: Path, dst: Path) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    os.link(src, dst)
    return src.stat().st_size


def link_tree(src: Path, dst: Path) -> tuple[int, int]:
    files = size = 0
    for root, dirs, names in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            if name in SKIP_NAMES:
                continue
            p = Path(root) / name
            size += link(p, dst / p.relative_to(src))
            files += 1
    return files, size


def check_frozen(run: Path) -> dict:
    frozen = json.loads((run / "FROZEN.json").read_text())
    for rel, want in frozen["sha256"].items():
        got = sha256(run / rel)
        if got != want:
            raise SystemExit(f"{rel}: sha256 {got} != frozen {want}; not packaging")
    return frozen


def metadata_rows(run: Path, rows: list[dict]) -> list[dict]:
    """One row per task for the Hub viewer (imagefolder): file_name relative to tasks/."""
    out = []
    for row in rows:
        t = json.loads((run / row["path"]).read_text())
        out.append(
            {
                "file_name": f"{t['task_id']}/{t['image']}",
                "task_id": t["task_id"],
                "family": t["family"],
                "camera": t["camera_id"],
                "question": t["question"],
                "answer_text": t["answer_text"],
                "answer": json.dumps(t["answer"]),
                "answer_type": t["answer_type"],
                "tolerance_cm": t["tolerance"],
                "shortcut_conflict": t["shortcut_conflict"],
                "scene_id": t["scene_id"],
                "twin_of": t["twin_of"],
                "clutter": (t.get("difficulty") or {}).get("clutter"),
                "camera_quality": (t.get("difficulty") or {}).get("camera_quality"),
            }
        )
    return out


def card(frozen: dict, stats: dict) -> str:
    tasks = frozen["tasks"]
    fam = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in tasks["by_family"].items())
    sha = "\n".join(f"| `{k}` | `{v}` |" for k, v in frozen["sha256"].items())
    gates = frozen["gates"]
    return f"""---
license: cc0-1.0
pretty_name: Visual Task Factory v1 (3D robot workcell)
language:
- en
task_categories:
- visual-question-answering
- image-text-to-text
tags:
- spatial-reasoning
- robotics
- synthetic
- blender
- 3d
- benchmark
- verifiable-rewards
size_categories:
- 1K<n<10K
configs:
- config_name: default
  data_files:
  - split: test
    path:
    - "tasks/metadata.jsonl"
    - "tasks/*/image.png"
---

# Visual Task Factory v1: spatial questions a robot must get right

{tasks["manifest"]:,} visual question-answer tasks about photoreal 3D robot-workcell scenes (a table of everyday objects seen through a robot's front, overhead and gripper cameras), rendered in Blender. Every answer is computed by a program from the 3D scene file and checked by model-free gates; no AI model wrote or checked an answer, and no AI image generator made an image.

- Code, recipes and the full pipeline: {GITHUB}
- Task Explorer (rotate the 3D scene, see each question, the true answer and every model's answer): {EXPLORER}
- Findings: the four Claude settings we tested (Haiku 5.5, Sonnet 5.5, Opus 5.5 at effort high, Opus 5.5 at xhigh) score about 25 points lower when the 2D image shortcut conflicts with the 3D truth ({GITHUB}/blob/main/docs/reports/v1/findings.md).

## What is in a task

`tasks/<task_id>/task.json` holds the question, the typed answer (`int`, `object`, `set`, `bool`, or `number` in cm with a tolerance), the program and parameters that computed it, the evidence objects, every gate's result and evidence, `shortcut_conflict` (does the 2D image shortcut give a different answer from the 3D truth), difficulty, seed and code versions. `image.png` is the 1024 x 768 camera image the question is about.

Families: {fam}. Cameras: front {tasks["by_rig"]["front"]}, overhead {tasks["by_rig"]["overhead"]}, gripper {tasks["by_rig"]["gripper"]}. Spatial tasks where the 2D shortcut conflicts with the truth: {tasks["by_shortcut_conflict"]["True"]}; where it agrees: {tasks["by_shortcut_conflict"]["False"]}; non-spatial: {tasks["by_shortcut_conflict"]["None"]}.

`tasks/metadata.jsonl` is a flat table of every task for the viewer (`answer` is JSON text).

## How answers are made and checked

1. A seeded sampler writes a scene file (`scenes/<scene_id>/scene.json`): table, objects with poses, gripper, zones, cameras, lights.
2. Blender settles the objects with rigid-body physics, writes the settled scene (`render/scene.settled.json`) and renders per camera a beauty image, a 16-bit object index, planar depth in metres (`depth.npz`) and amodal masks; a seeded camera-quality dial adds lens distortion, noise and a CCTV look.
3. A typed program computes each answer from the settled scene file (G1). The gates check it model-free: G2 recomputes it from the index and depth pixels; G3 evidence visible; G4 no near-ties; G5 balanced answers and a question-only baseline; G6 counterfactual twins (edit the scene, re-render, the answer must change; the {stats["twin_scenes"]} twin scenes are in `twins/`); G7 determinism hashes.

Gate pass rates on the {gates["G1"]["run"]:,} questions that reached the gates: G1 {gates["G1"]["pass_rate"]:.1%}, G2 {gates["G2"]["pass_rate"]:.1%}, G3 {gates["G3"]["pass_rate"]:.1%}, G4 {gates["G4"]["pass_rate"]:.1%}, G7 {gates["G7"]["pass_rate"]:.1%}; G6 {gates["G6"]["pass"]} of {gates["G6"]["run"]} valid twins changed the answer. Every rejected question is in `rejected.jsonl` with its reason; the funnel and baselines are in {GITHUB}/blob/main/docs/VALIDATION.md. Prompts, scoring and model settings: {GITHUB}/blob/main/docs/EVAL_PROTOCOL.md.

## Files

| Path | What |
| --- | --- |
| `tasks/` | {tasks["manifest"]:,} tasks (task.json + image.png) and `metadata.jsonl` |
| `manifest.jsonl` | One row per task: id, scene, camera, family, answer type, shortcut flag, path |
| `scenes/` | {stats["base_scenes"]} scenes: scene file, settled scene, render manifest, beauty, index, depth, amodal masks per camera, gate results |
| `twins/` | {stats["twin_scenes"]} G6 twin scenes, same layout |
| `rejected.jsonl`, `validate_report.json` | Every rejection with its reason; gate statistics |
| `baselines/` | G5: answer balance, prior-only and question-only baselines |
| `eval/`, `sweep/` | Answers of Claude Sonnet 5.5 (1,500 tasks, and question-only), and of all four Claude settings on the same 1,567 tasks |
| `report/index.html` | The dataset report (open in a browser) |
| `run.json`, `FROZEN.json` | Recipe, seed, per-scene seeds; the freeze record |

## Reproduce and verify

The folder is a run folder of the factory. Clone the code, download this dataset into `out/v1`, then rebuild scenes from their seeds and compare. Scene files, settled scenes, answers, object masks and depth come back exact; how close the re-rendered images are, and why, is measured in {GITHUB}/blob/main/docs/reports/v1/verify_showcase.md.

```bash
git clone {GITHUB} && cd hrishikesh-visual-task-factory && uv sync
hf download {REPO} --repo-type dataset --local-dir out/v1
uv run python assets/fetch_manifest.py         # the CC0 Poly Haven models (about 60 MB)
uv run vtf verify-showcase out/v1              # 3 scenes: once on the GPU, twice on the CPU
```

## Known issue: simplified materials on some objects

`vtf verify` found a renderer bug after the freeze. Scenes were rendered in batches, one Blender process per batch, and after each render the export of the 3D-viewer model (`scene.glb`) swapped the scene's object materials for simplified ones in place, on asset meshes the process keeps for later scenes. So an object already drawn by an earlier scene in the same process shows its base-colour texture at 256 px without normal, roughness or metal maps (a brass pot looks matte, a printed label blurs). This can affect 248 of the 261 scenes. Geometry, masks, depth and every answer are unaffected: they come from the scene file and the data passes. v1 stays as frozen; details in {GITHUB}/blob/main/docs/reports/v1/verify_showcase.md.

## Frozen hashes

Dataset v1 was frozen on 24 Nov 2026 at {frozen["scenes_in_v1"]} (code {frozen["code_commit"].split()[0]}, Blender {frozen["versions"]["blender"]}, recipe `{frozen["recipe"]}`, run seed {frozen["run_seed"]}).

| File | sha256 |
| --- | --- |
{sha}

## Licence and sources

CC0 1.0. The 3D models and the HDRI are from Poly Haven (CC0); four objects are procedural primitives of exact size. Images were rendered with Blender Cycles; no AI image generator is used anywhere.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, default=Path("out/v1"))
    parser.add_argument("--out", type=Path, default=Path("out/hf_release"))
    parser.add_argument("--card-only", action="store_true", help="Rewrite README.md only.")
    args = parser.parse_args()
    run, out = args.run, args.out
    if args.card_only:
        frozen = check_frozen(run)
        stats = {
            "base_scenes": sum(1 for p in (out / "scenes").iterdir() if p.is_dir()),
            "twin_scenes": sum(1 for p in (out / "twins").iterdir() if p.is_dir()),
        }
        (out / "README.md").write_text(card(frozen, stats))
        print(f"hf_release: rewrote {out / 'README.md'}")
        return
    if os.stat(run).st_dev != os.stat(out.parent if out.parent.exists() else ".").st_dev:
        raise SystemExit("--out must be on the same disk as --run (hard links)")
    frozen = check_frozen(run)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    rows = [json.loads(line) for line in (run / "manifest.jsonl").read_text().splitlines()]
    assert len(rows) == frozen["tasks"]["manifest"], "manifest does not match FROZEN.json"
    n_files = size = 0
    for row in rows:
        f, s = link_tree((run / row["path"]).parent, out / Path(row["path"]).parent)
        n_files, size = n_files + f, size + s

    scene_ids = sorted({r["scene_id"] for r in rows})
    base = [s for s in scene_ids if (run / "scenes" / s / "scene.json").exists()]
    twins = [s for s in scene_ids if s not in base]
    for s in base:
        f, sz = link_tree(run / "scenes" / s, out / "scenes" / s)
        n_files, size = n_files + f, size + sz
    for s in twins:
        f, sz = link_tree(run / "twins" / s, out / "twins" / s)
        n_files, size = n_files + f, size + sz

    for name in TOP_FILES:
        size += link(run / name, out / name)
        n_files += 1
    for d in TOP_DIRS:
        f, sz = link_tree(run / d, out / d)
        n_files, size = n_files + f, size + sz
    for model_dir in sorted((run / "sweep").iterdir()):
        if model_dir.is_dir():
            f, sz = link_tree(model_dir, out / "sweep" / model_dir.name)
            n_files, size = n_files + f, size + sz
    for name in SWEEP_FILES:
        size += link(run / "sweep" / name, out / "sweep" / name)
    task_set = json.loads((run / "sweep" / "task_set.json").read_text())
    task_set["run"] = "out/v1"  # the original holds a machine path
    (out / "sweep" / "task_set.json").write_text(json.dumps(task_set, indent=2) + "\n")

    meta = metadata_rows(run, rows)
    (out / "tasks" / "metadata.jsonl").write_text(
        "".join(json.dumps(m, sort_keys=True) + "\n" for m in meta)
    )
    stats = {"base_scenes": len(base), "twin_scenes": len(twins)}
    (out / "README.md").write_text(card(frozen, stats))

    fams = Counter(m["family"] for m in meta)
    print(
        f"hf_release: {len(rows)} tasks, {len(base)} scenes, {len(twins)} twin scenes, "
        f"{n_files} files linked ({size / 1e9:.2f} GB, no extra disk), families {dict(fams)}"
    )
    print(f"  -> {out} (README.md data card, tasks/metadata.jsonl for the viewer)")


if __name__ == "__main__":
    main()
