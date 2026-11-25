"""`vtf twin sim-to-real <register out>`: the same question on a real photo and on its synthetic
sibling (same layout, same camera pose, same answer), per model.

    uv run vtf twin sim-to-real out/twins_real --model claude-sonnet-5-5 --model claude-opus-5-5

Reads <out>/pairs.jsonl (written by `vtf twin register`), evaluates each model on the paired
real tasks (<out>/real) and synthetic tasks (<out>/sim) with the ordinary eval harness (cached,
so a rerun costs nothing), then reports per model: accuracy on photos and on renders, the
difference (photo minus render), 95% intervals from a bootstrap over layouts (tasks of one
layout are correlated; the same draws for every model, so pairs stay paired), the discordant
pairs and an exact McNemar p-value, and the split by question family. Writes
<out>/sim_to_real/summary.json and summary.md.
"""

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from vtf.core.io import read_jsonl
from vtf.core.layout import EVAL_RESULTS, RunLayout
from vtf.core.rng import derive_seed

PAIRS = "pairs.jsonl"
OUT_DIR = "sim_to_real"
RESAMPLES = 2000


def _results(run: Path, key: str) -> dict[str, bool]:
    path = RunLayout(run).eval_dir(key) / EVAL_RESULTS
    return {r["task_id"]: bool(r["correct"]) for r in read_jsonl(path)}


def mcnemar_p(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value for b and c discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return round(min(1.0, 2 * tail), 4)


def layout_bootstrap(
    pairs: list[dict], real: dict[str, bool], sim: dict[str, bool], resamples: int = RESAMPLES
) -> dict:
    """95% percentile intervals of photo accuracy, render accuracy and their difference,
    resampling layouts with replacement (seeded by the layout list, so every model gets the same
    draws)."""
    by_layout: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for p in pairs:
        by_layout[p["layout"]].append((int(real[p["real_task_id"]]), int(sim[p["sim_task_id"]])))
    layouts = sorted(by_layout)
    sums = np.array([[sum(r for r, _ in by_layout[k]), sum(s for _, s in by_layout[k]),
                      len(by_layout[k])] for k in layouts], dtype=float)  # fmt: skip
    rng = np.random.default_rng(derive_seed("sim_to_real", *layouts, resamples))
    draws = rng.integers(0, len(layouts), size=(resamples, len(layouts)))
    tot = sums[draws].sum(axis=1)  # (resamples, 3)
    real_acc, sim_acc = tot[:, 0] / tot[:, 2], tot[:, 1] / tot[:, 2]

    def ci(x: np.ndarray) -> list[float]:
        lo, hi = np.percentile(x, [2.5, 97.5])
        return [round(float(lo), 4), round(float(hi), 4)]

    return {"real": ci(real_acc), "sim": ci(sim_acc), "difference": ci(real_acc - sim_acc)}


def compare(pairs: list[dict], real: dict[str, bool], sim: dict[str, bool]) -> dict:
    pairs = [p for p in pairs if p["real_task_id"] in real and p["sim_task_id"] in sim]
    n = len(pairs)
    r = [real[p["real_task_id"]] for p in pairs]
    s = [sim[p["sim_task_id"]] for p in pairs]
    b = sum(x and not y for x, y in zip(r, s, strict=True))  # photo right, render wrong
    c = sum(y and not x for x, y in zip(r, s, strict=True))  # render right, photo wrong
    fam: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
    for p, x, y in zip(pairs, r, s, strict=True):
        fam[p["family"]].append((x, y))
    out = {
        "pairs": n,
        "layouts": len({p["layout"] for p in pairs}),
        "accuracy_real": round(sum(r) / n, 4) if n else None,
        "accuracy_sim": round(sum(s) / n, 4) if n else None,
        "difference": round((sum(r) - sum(s)) / n, 4) if n else None,
        "photo_right_render_wrong": b,
        "render_right_photo_wrong": c,
        "mcnemar_p": mcnemar_p(b, c),
        "families": {
            f: {"n": len(v), "real": round(sum(x for x, _ in v) / len(v), 4),
                "sim": round(sum(y for _, y in v) / len(v), 4)}
            for f, v in sorted(fam.items())
        },  # fmt: skip
    }
    if n:
        out["ci95"] = layout_bootstrap(pairs, real, sim)
    return out


def sim_to_real(out: Path, models: list[str], limit: int | None = None) -> dict:
    from vtf.eval.config import resolve_model
    from vtf.eval.runner import eval_run

    out = Path(out)
    pairs = list(read_jsonl(out / PAIRS))
    if limit:
        pairs = pairs[:limit]
    if not pairs:
        raise SystemExit(f"vtf twin sim-to-real: no pairs in {out / PAIRS}; run vtf twin register")
    report = {"run": str(out), "pairs_file": str(out / PAIRS), "resamples": RESAMPLES,
              "models": {}}  # fmt: skip
    for model in models:
        key = resolve_model(model).key
        quiet = lambda *_: None  # noqa: E731
        eval_run(out / "real", model, task_ids=[p["real_task_id"] for p in pairs], echo=quiet)
        eval_run(out / "sim", model, task_ids=[p["sim_task_id"] for p in pairs], echo=quiet)
        stats = compare(pairs, _results(out / "real", key), _results(out / "sim", key))
        report["models"][key] = stats
        ci = stats.get("ci95", {})
        print(
            f"{key}: photos {stats['accuracy_real']:.1%} {ci.get('real')}, renders "
            f"{stats['accuracy_sim']:.1%} {ci.get('sim')}, difference {stats['difference']:+.1%} "
            f"{ci.get('difference')} over {stats['pairs']} pairs from {stats['layouts']} layouts; "
            f"McNemar p {stats['mcnemar_p']}"
        )
    dest = out / OUT_DIR
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    (dest / "summary.md").write_text(markdown(report))
    print(f"vtf twin sim-to-real: wrote {dest / 'summary.json'} and summary.md")
    return report


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v:.1%}"


def _ci(v: list[float] | None) -> str:
    return "-" if not v else f"{v[0]:.1%} to {v[1]:.1%}"


def markdown(report: dict) -> str:
    lines = [
        "# Sim-to-real: photos of the physical twins vs their synthetic siblings",
        "",
        "Same layout, same camera pose (estimated from the photo's markers), same question and "
        "answer; only the image differs. Intervals: 95%, bootstrap over layouts.",
        "",
        "| Model | Pairs | Layouts | Photo accuracy | Render accuracy | Photo minus render | "
        "Discordant (photo only / render only) | McNemar p |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key, s in report["models"].items():
        ci = s.get("ci95", {})
        lines.append(
            f"| {key} | {s['pairs']} | {s['layouts']} | {_pct(s['accuracy_real'])} "
            f"({_ci(ci.get('real'))}) | {_pct(s['accuracy_sim'])} ({_ci(ci.get('sim'))}) | "
            f"{s['difference']:+.1%} ({_ci(ci.get('difference'))}) | "
            f"{s['photo_right_render_wrong']} / {s['render_right_photo_wrong']} | "
            f"{s['mcnemar_p']} |"
        )
    lines += ["", "## By family", "", "| Model | Family | n | Photo | Render |",
              "| --- | --- | --- | --- | --- |"]  # fmt: skip
    for key, s in report["models"].items():
        for fam, v in s["families"].items():
            lines.append(f"| {key} | {fam} | {v['n']} | {_pct(v['real'])} | {_pct(v['sim'])} |")
    lines.append("")
    return "\n".join(lines)
