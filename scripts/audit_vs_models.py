"""The founder's blind audit beside every model, on the same tasks (brief 06, phase C item 5).

Scores the audit export with `vtf audit score` (the eval scorer), then puts each sweep model's
answers to the same audited tasks next to it: overall and split by shortcut_conflict, with 95%
intervals from a scene bootstrap, and the paired human-minus-model difference.

Run it once the founder has exported the audit page's JSON:

  uv run python scripts/audit_vs_models.py --run <out/v1> --answers <audit export .json>

Writes docs/reports/v1/human_vs_models.json and .md (and <run>/audit/score.json via the scorer).

Distance and free-space answers come from the "scale stated" condition (<run>/sweep_scale/,
brief VTF-08): the founder audits with the grid scale known, so the models must have it too.
--scale-not-stated uses the original answers instead.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shortcut_findings import MODELS, SceneBoot, ci  # noqa: E402

from vtf.core.io import read_jsonl  # noqa: E402
from vtf.eval.runner import manifest_rows  # noqa: E402
from vtf.package.audit import _entries, score_audit  # noqa: E402


def label(v: bool | None) -> str:
    return "n/a" if v is None else ("conflict" if v else "no_conflict")


def stats(boot: SceneBoot, rows: list[dict], who: str, mask=lambda r: True) -> dict[str, Any]:
    num = boot.sums(rows, lambda r: r[who], mask)
    den = boot.sums(rows, lambda r: 1, mask)
    point, draws = boot.ratio(num, den)
    return {
        "n": int(den.sum()),
        "correct": int(num.sum()),
        "accuracy": None if point is None else round(float(point), 4),
        "ci95": ci(draws),
        "_draws": draws,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--answers", required=True)
    ap.add_argument("--out", default="docs/reports/v1")
    ap.add_argument("--audit-dir", default=None, help="default <run>/audit")
    ap.add_argument("--scale-not-stated", action="store_true",
                    help="distance, free space: original answers, not sweep_scale/")  # fmt: skip
    args = ap.parse_args()
    run = Path(args.run).resolve()

    full = score_audit(run, args.answers, audit_dir=args.audit_dir)
    # the scorer returns the misses; every other audited task (answered or flagged) is correct
    entries = _entries(json.loads(Path(args.answers).read_text()))
    miss_ids = {m["task_id"] for m in full["misses"]}
    scene = {r.task_id: r.scene_id for r in manifest_rows(run)}
    conflict = {r.task_id: r.shortcut_conflict for r in manifest_rows(run)}
    audited = [
        t
        for t, e in entries.items()
        if t in scene and (e.get("answer") not in (None, "") or e.get("flag"))
    ]
    human = {t: t not in miss_ids for t in audited}

    # the 1,567-task sweep, plus scripts/twin_sweep.py's answers (the twin pairs and the one
    # task of the refreshed audit, sample_v2, that no model had answered); first, for distance
    # and free space, the scale-stated answers (scripts/ask_sweep.py --condition scale)
    folders = ("sweep", "sweep_twins") if args.scale_not_stated else (
        "sweep_scale", "sweep", "sweep_twins")  # fmt: skip
    models: dict[str, dict[str, Any]] = {}
    for key, name, effort in MODELS:
        got: dict[str, bool] = {}
        for folder in folders:
            path = run / folder / key / "results.jsonl"
            if path.is_file():
                for r in read_jsonl(path):
                    if r.get("error") != "no reply":
                        got.setdefault(r["task_id"], bool(r["correct"]))
        if got:
            models[key] = {"label": f"{name} ({effort})", "answers": got}

    rows = []
    for t in audited:
        base = scene[t].split("_twin_")[0]  # a G6 twin is clustered with its source scene
        row = {"task_id": t, "cluster": base, "conflict": conflict[t], "human": human[t]}
        for key, m in models.items():
            row[key] = m["answers"].get(t)
        rows.append(row)
    boot = SceneBoot(sorted({r["cluster"] for r in rows}))

    out: dict[str, Any] = {
        "audited": len(rows),
        "human_accuracy_from_scorer": full.get("accuracy"),
        "splits": {},
    }
    for split in ("all", "conflict", "no_conflict", "n/a"):

        def mask(r: dict, split: str = split) -> bool:
            return split == "all" or label(r["conflict"]) == split

        h = stats(boot, rows, "human", mask)
        entry = {"human": h, "models": {}}
        for key, m in models.items():
            have = [r for r in rows if r[key] is not None]
            s = stats(boot, have, key, mask)
            hs = stats(boot, have, "human", mask)
            diff = (
                None
                if s["accuracy"] is None or hs["accuracy"] is None
                else round(hs["accuracy"] - s["accuracy"], 4)
            )
            entry["models"][key] = {
                "label": m["label"],
                **s,
                "human_minus_model": diff,
                "human_minus_model_ci95": ci(hs["_draws"] - s["_draws"]),
            }
        out["splits"][split] = entry

    def strip(o: Any) -> Any:
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if not k.startswith("_")}
        return o

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "human_vs_models.json").write_text(json.dumps(strip(out), indent=2) + "\n")

    def cell(s: dict) -> str:
        if not s["n"] or s["accuracy"] is None:
            return "–"
        lo, hi = s["ci95"] or (None, None)
        rng = f" ({lo * 100:.0f} to {hi * 100:.0f}%)" if lo is not None else ""
        return f"{s['accuracy'] * 100:.0f}%{rng}, n {s['n']}"

    lines = [
        "# Founder audit beside the models",
        "",
        f"The founder answered {len(rows)} of the 150 blind audit tasks without seeing the truth. "
        "Every answer is scored by the same scorer as the models. Intervals are 95%, from a "
        "bootstrap over scenes. "
        + ("Distance and free-space model answers are the original ones (scale not stated)."
           if args.scale_not_stated else
           "Distance and free-space model answers come from the scale-stated condition (the "
           "question names the grid's 1 cm squares, or the reference object's size), as the "
           "founder saw the grid's scale too."),  # fmt: skip
        "",
        "| Who | All tasks | Shortcut conflicts | No conflict | Non-spatial |",
        "| --- | --- | --- | --- | --- |",
        "| Founder | "
        + " | ".join(
            cell(out["splits"][s]["human"]) for s in ("all", "conflict", "no_conflict", "n/a")
        )
        + " |",
    ]
    for key, m in models.items():
        lines.append(
            f"| {m['label']} | "
            + " | ".join(
                cell(out["splits"][s]["models"][key])
                for s in ("all", "conflict", "no_conflict", "n/a")
            )
            + " |"
        )
    lines += ["", "Founder minus model, percentage points (95% interval):", ""]
    for key, m in models.items():
        parts = []
        for s in ("all", "conflict", "no_conflict"):
            e = out["splits"][s]["models"][key]
            if e["human_minus_model"] is None:
                continue
            c = e["human_minus_model_ci95"]
            rng = f" ({c[0] * 100:+.0f} to {c[1] * 100:+.0f})" if c else ""
            parts.append(f"{s.replace('_', ' ')} {e['human_minus_model'] * 100:+.0f}{rng}")
        lines.append(f"- {m['label']}: " + "; ".join(parts))
    (out_dir / "human_vs_models.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
