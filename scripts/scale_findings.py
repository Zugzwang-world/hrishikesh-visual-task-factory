"""Scale not stated vs stated (brief VTF-08, Phase 1 item 2): the same distance and free-space
tasks, asked to the four Claude settings without and with the sentence that states the scale.

Not stated = the original answers (cache key PROMPT_VERSION, the frozen sweep's replies); stated =
scripts/ask_sweep.py --condition scale (cache key PROMPT_VERSION+scale.1, <run>/sweep_scale/).
Both are re-scored here from the cache with the eval scorer, task by task, so the comparison is
paired. Intervals: 95%, 2,000 scene resamples (the same draws as the other findings), stated minus
not stated computed on each draw.

  uv run python scripts/scale_findings.py --run out/v1

Writes docs/reports/v1/scale_stated.json and .md.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shortcut_findings import MODELS, SceneBoot, ci  # noqa: E402

from vtf.core.io import read_task  # noqa: E402
from vtf.core.layout import RunLayout  # noqa: E402
from vtf.eval.cache import EvalCache  # noqa: E402
from vtf.eval.prompt import PROMPT_VERSION  # noqa: E402
from vtf.eval.runner import FINAL_STOPS, manifest_rows, score_task  # noqa: E402
from vtf.eval.scale import scale_prompt_version, task_scale  # noqa: E402

SPLITS = {
    "all": lambda r: True,
    "distance": lambda r: r["family"] == "distance",
    "free_space": lambda r: r["family"] == "free_space",
    "grid": lambda r: r["scale"] == "grid",
    "reference": lambda r: r["scale"] == "reference",
}


def correct(cache: EvalCache, task, key: str, pv: str) -> bool | None:
    hit = cache.get(task.task_id, key, pv)
    if hit is None:
        return None
    if hit.stop_reason not in FINAL_STOPS:
        return False
    return bool(score_task(task, hit.raw_text).correct)


def stat(boot: SceneBoot, rows: list[dict], col: str, mask) -> tuple[dict[str, Any], Any]:
    num = boot.sums(rows, lambda r: r[col], mask)
    den = boot.sums(rows, lambda r: 1, mask)
    point, draws = boot.ratio(num, den)
    return {"n": int(den.sum()), "correct": int(num.sum()),
            "accuracy": None if point is None else round(float(point), 4),
            "ci95": ci(draws)}, draws  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--name", default="sweep_scale")
    ap.add_argument("--out", default="docs/reports/v1")
    ap.add_argument("--cache", type=Path, default=None)
    args = ap.parse_args()
    run = Path(args.run).resolve()
    cache_path = Path(args.cache or os.environ.get("VTF_EVAL_CACHE")
                      or run.parent / "cache" / "eval_cache.sqlite")  # fmt: skip
    ids = json.loads((run / args.name / "task_set.json").read_text())["task_ids"]
    by_id = {r.task_id: r for r in manifest_rows(run)}
    tasks = {t: read_task(RunLayout(run).root / by_id[t].path) for t in ids}
    out: dict[str, Any] = {"run": f"out/{run.name}", "tasks": len(ids), "models": {},
                           "prompt_versions": {"not_stated": PROMPT_VERSION,
                                               "stated": scale_prompt_version()}}  # fmt: skip
    with EvalCache(cache_path) as cache:
        per_model: dict[str, list[dict]] = {}
        for key, _, _ in MODELS:
            rows = []
            for t in ids:
                task = tasks[t]
                a = correct(cache, task, key, PROMPT_VERSION)
                b = correct(cache, task, key, scale_prompt_version())
                if a is None or b is None:
                    continue
                rows.append({"task_id": t, "cluster": task.scene_id.split("_twin_")[0],
                             "family": task.family, "scale": (task_scale(task) or {}).get("kind"),
                             "not_stated": a, "stated": b})  # fmt: skip
            per_model[key] = rows
    boot = SceneBoot(sorted({r["cluster"] for rows in per_model.values() for r in rows}))
    for key, name, effort in MODELS:
        rows = per_model[key]
        m: dict[str, Any] = {"label": f"{name} ({effort})", "paired_tasks": len(rows),
                             "splits": {}}  # fmt: skip
        for split, mask in SPLITS.items():
            a, da = stat(boot, rows, "not_stated", mask)
            b, db = stat(boot, rows, "stated", mask)
            diff = None if a["accuracy"] is None else round(b["accuracy"] - a["accuracy"], 4)
            m["splits"][split] = {
                "not_stated": a,
                "stated": b,
                "stated_minus_not": diff,
                "stated_minus_not_ci95": ci(db - da),
                "flipped_to_right": sum(1 for r in rows if mask(r) and r["stated"]
                                        and not r["not_stated"]),
                "flipped_to_wrong": sum(1 for r in rows if mask(r) and r["not_stated"]
                                        and not r["stated"]),
            }  # fmt: skip
        out["models"][key] = m

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scale_stated.json").write_text(json.dumps(out, indent=2) + "\n")

    def pct(s: dict) -> str:
        return "–" if s["accuracy"] is None else f"{s['accuracy'] * 100:.0f}%"

    def pp(e: dict) -> str:
        if e["stated_minus_not"] is None:
            return "–"
        c = e["stated_minus_not_ci95"]
        rng = f" ({c[0] * 100:+.0f} to {c[1] * 100:+.0f})" if c else ""
        return f"{e['stated_minus_not'] * 100:+.0f}{rng}"

    n_grid = sum(1 for t in ids if (task_scale(tasks[t]) or {}).get("kind") == "grid")
    lines = [
        "# Scale stated: distance and free space",
        "",
        f"The {len(ids)} distance and free-space tasks among the 1,567 eval tasks and the 150 "
        "audit tasks, asked again to every Claude setting with one sentence in front of the "
        f"question. {n_grid} sit on the printed grid and get \"The table has a printed grid of "
        "1 cm squares with bold lines every 10 cm.\"; the other "
        f"{len(ids) - n_grid} have no grid and use a procedural reference object for scale, so "
        "they get that object's exact size instead (\"The cube is exactly 5 cm on each side.\"), "
        "because the grid sentence would be false there. Everything else is unchanged. Paired "
        "on the same tasks; 95% intervals from 2,000 scene resamples. Frozen v1 results are "
        "untouched; these answers are in out/v1/sweep_scale/.",
        "",
        "| Setting | Not stated | Stated | Change, points (95% interval) | Distance | "
        "Free space | Grid scenes | Reference scenes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for m in out["models"].values():
        s = m["splits"]
        lines.append(
            f"| {m['label']} | {pct(s['all']['not_stated'])} | {pct(s['all']['stated'])} | "
            f"{pp(s['all'])} | {pp(s['distance'])} | {pp(s['free_space'])} | {pp(s['grid'])} | "
            f"{pp(s['reference'])} |"
        )
    lines += ["", "Tasks that flipped (wrong to right / right to wrong) when the scale was stated:",
              ""]  # fmt: skip
    for m in out["models"].values():
        a = m["splits"]["all"]
        lines.append(f"- {m['label']}: {a['flipped_to_right']} / {a['flipped_to_wrong']} of "
                     f"{m['paired_tasks']}")  # fmt: skip
    (out_dir / "scale_stated.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
