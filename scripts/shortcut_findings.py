"""The shortcut analysis on frozen v1 (brief 06, phase C).

Reads the sweep results (<run>/sweep/<model>/results.jsonl, written by scripts/model_sweep.py)
and the task packages, and writes:

  docs/reports/v1/findings.json   every number below
  docs/reports/v1/findings.md     the write-up tables (prose is added by hand around them)
  docs/media/shortcut_conflict_accuracy.png / .svg   the headline chart

1. Accuracy per model and effort: overall, by shortcut_conflict, family and camera, with 95%
   intervals from a bootstrap that resamples whole scenes (tasks from one scene are correlated).
2. Shortcut capture vs chance: among answered conflict misses, the share equal to the 2D-shortcut
   answer (scored with the eval scorer against shortcut_answer), against the chance share (per
   task 1 / number of wrong candidates, averaged), and their ratio, all with scene intervals.
3. Confound check: the matched within-stratum gap (conflict minus non-conflict accuracy inside
   each family x camera stratum, weighted by the stratum's conflict tasks) and a logistic
   regression of correct on conflict, visible fraction, G4 margin, object count, camera quality
   level and family (IRLS, scene-clustered standard errors, plus a scene bootstrap).
4. Does thinking help: the conflict gap for Opus high vs xhigh, and across Haiku, Sonnet, Opus.

Usage: uv run python scripts/shortcut_findings.py --run <out/v1> [--out docs/reports/v1]
"""

import argparse
import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from vtf.core.io import read_jsonl, read_task
from vtf.core.layout import RunLayout
from vtf.eval.runner import manifest_rows, score_task

MODELS = [
    ("claude-haiku-5-5", "Haiku 5.5", "high"),
    ("claude-sonnet-5-5", "Sonnet 5.5", "high"),
    ("claude-opus-5-5", "Opus 5.5", "high"),
    ("claude-opus-5-5-xhigh", "Opus 5.5", "xhigh"),
]
B = 2000
B_LOGIT = 500
SEED = 20261124
QUALITY = {"clean": 0, "mild": 1, "harsh": 2, "cctv": 3}
OBJECT_CHOICE = ("nearest_to_gripper", "relation", "occlusion")
CAMERA_NAMES = {"front": "front", "overhead": "overhead", "gripper": "wrist"}


# --- loading ----------------------------------------------------------------------------------


def load_tasks(run: Path, ids: list[str]) -> dict[str, dict[str, Any]]:
    rows = {r.task_id: r for r in manifest_rows(run)}
    lay = RunLayout(run)
    out = {}
    for tid in ids:
        row = rows[tid]
        task = read_task(lay.root / row.path)
        diff = task.difficulty or {}
        g4 = (task.gates or {}).get("G4")
        if g4 is not None and hasattr(g4, "model_dump"):
            g4 = g4.model_dump()
        g4e = (g4 or {}).get("evidence") or {}
        out[tid] = {
            "task": task,
            "scene_id": row.scene_id,
            "cluster": base_scene(row.scene_id),
            "family": task.family,
            "camera": task.camera_id,
            "conflict": task.shortcut_conflict,
            "n_objects": diff.get("n_objects"),
            "visible": diff.get("min_visible_fraction", diff.get("evidence_min_visible")),
            "quality": QUALITY.get(str(diff.get("camera_quality")), None),
            "g4_margin": g4_margin(g4e),
            "no_runner_up": bool(g4e.get("no_runner_up")),
            "n_candidates": diff.get("n_candidates"),
        }
    # relation tasks with no runner-up have no finite margin: give them the largest observed
    top = max((v["g4_margin"] for v in out.values() if v["g4_margin"] is not None), default=0.0)
    for v in out.values():
        if v["g4_margin"] is None and v["no_runner_up"]:
            v["g4_margin"] = top
    return out


def base_scene(scene_id: str) -> str:
    """A G6 twin (scene_0012_twin_<hash>) is clustered with the scene it was made from."""
    return scene_id.split("_twin_")[0]


def g4_margin(ev: dict[str, Any]) -> float | None:
    """log(margin / gate threshold): unit-free, 0 at the gate."""
    if "metric_margin_m" in ev and ev.get("metric_threshold_m"):
        return math.log(max(ev["metric_margin_m"], 1e-6) / ev["metric_threshold_m"])
    occ = ev.get("occlusion")
    if isinstance(occ, dict) and "winner_share" in occ:
        gap = occ["winner_share"] - occ.get("runner_up_share", 0.0)
        return math.log(max(gap, 1e-4) / 0.03)
    return None


def wrong_candidates(info: dict[str, Any]) -> int:
    """How many wrong answers a conflict task offers (chance of the shortcut = 1 / this).

    Yes/no and two-object questions offer one; object questions every other nameable object in
    the view (minus the anchor or occluder, which cannot be the answer; for relation not only
    the objects inside the direction cone, since a model can name any visible object); set
    questions the sets one object away from the truth (one per object)."""
    task = info["task"]
    k = len(task.scene_objects or [])
    fam = info["family"]
    if task.answer_type == "bool" or fam == "front_to_back":
        return 1
    if fam == "nearest_to_gripper":
        return max(1, k - 1)
    if fam == "relation":
        # any visible object but the anchor can be named, as for nearest and occlusion
        return max(1, k - 2)
    if fam == "occlusion":
        return max(1, k - 2)
    if task.answer_type == "set":
        return max(1, k)
    return max(1, k - 1)


APPROX: set[str] = set()


def noun(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().lower().split()[-1]


def load_model(run: Path, key: str, tasks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    path = run / "sweep" / key / "results.jsonl"
    if not path.is_file():
        return []
    rows = []
    for rec in read_jsonl(path):
        tid = rec["task_id"]
        if tid not in tasks:
            continue
        info = tasks[tid]
        shortcut = None
        chance = None
        task = info["task"]
        miss = not rec["correct"] and rec["parse_ok"]
        if info["conflict"] is True and miss and task.shortcut_answer is not None:
            as_shortcut = task.model_copy(update={"answer": task.shortcut_answer})
            try:
                shortcut = bool(score_task(as_shortcut, rec["raw_response"]).correct)
            except ValueError:
                # the shortcut object is not among the task's nameable objects (6 relation
                # tasks in v1): compare nouns instead
                shortcut = noun(rec.get("parsed")) == noun(task.shortcut_answer)
                APPROX.add(tid)
            chance = 1.0 / wrong_candidates(info)
        rows.append(
            {
                "task_id": tid,
                "scene_id": info["scene_id"],
                "cluster": info["cluster"],
                "family": info["family"],
                "camera": info["camera"],
                "conflict": info["conflict"],
                "correct": bool(rec["correct"]),
                "parse_ok": bool(rec["parse_ok"]),
                "error": rec.get("error"),
                "shortcut": shortcut,
                "chance": chance,
                "thinking": rec.get("thinking_tokens"),
                "output": rec.get("output_tokens"),
                "input": rec.get("input_tokens"),
                "parsed": rec.get("parsed"),
            }
        )
    return rows


# --- scene bootstrap --------------------------------------------------------------------------


class SceneBoot:
    """Shared scene-resampling weights: every statistic uses the same B draws, so differences
    between models (same scenes) are paired."""

    def __init__(self, scenes: list[str], b: int = B, seed: int = SEED) -> None:
        self.scenes = scenes
        self.index = {s: i for i, s in enumerate(scenes)}
        rng = np.random.default_rng(seed)
        s = len(scenes)
        draws = rng.integers(0, s, size=(b, s))
        self.w = np.stack([np.bincount(d, minlength=s) for d in draws]).astype(float)

    def sums(self, rows: list[dict], value, mask=lambda r: True) -> np.ndarray:
        out = np.zeros(len(self.scenes))
        for r in rows:
            if mask(r):
                out[self.index[r["cluster"]]] += float(value(r))
        return out

    def ratio(self, num: np.ndarray, den: np.ndarray) -> tuple[float | None, np.ndarray]:
        point = num.sum() / den.sum() if den.sum() else None
        with np.errstate(invalid="ignore", divide="ignore"):
            draws = (self.w @ num) / (self.w @ den)
        return point, draws


def ci(draws: np.ndarray) -> list[float] | None:
    d = draws[np.isfinite(draws)]
    if len(d) < 10:
        return None
    lo, hi = np.percentile(d, [2.5, 97.5])
    return [round(float(lo), 4), round(float(hi), 4)]


def acc_stat(boot: SceneBoot, rows: list[dict], mask=lambda r: True) -> dict[str, Any]:
    num = boot.sums(rows, lambda r: r["correct"], mask)
    den = boot.sums(rows, lambda r: 1, mask)
    point, draws = boot.ratio(num, den)
    return {
        "n": int(den.sum()),
        "correct": int(num.sum()),
        "accuracy": None if point is None else round(float(point), 4),
        "ci95": ci(draws),
        "_draws": draws,
    }


def strip(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: strip(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [strip(v) for v in obj]
    return obj


# --- analyses ---------------------------------------------------------------------------------


def conflict_label(v: bool | None) -> str:
    return "n/a" if v is None else ("conflict" if v else "no_conflict")


def accuracy_table(boot: SceneBoot, rows: list[dict]) -> dict[str, Any]:
    out: dict[str, Any] = {"overall": acc_stat(boot, rows)}
    out["by_conflict"] = {
        lab: acc_stat(boot, rows, lambda r, lab=lab: conflict_label(r["conflict"]) == lab)
        for lab in ("conflict", "no_conflict", "n/a")
    }
    out["by_family"] = {
        f: acc_stat(boot, rows, lambda r, f=f: r["family"] == f)
        for f in sorted({r["family"] for r in rows})
    }
    out["by_camera"] = {
        c: acc_stat(boot, rows, lambda r, c=c: r["camera"] == c)
        for c in sorted({r["camera"] for r in rows})
    }
    out["by_family_conflict"] = {
        f"{f}|{lab}": acc_stat(
            boot,
            rows,
            lambda r, f=f, lab=lab: r["family"] == f and conflict_label(r["conflict"]) == lab,
        )
        for f in sorted({r["family"] for r in rows if r["conflict"] is not None})
        for lab in ("conflict", "no_conflict")
    }
    conf = out["by_conflict"]["conflict"]["_draws"]
    non = out["by_conflict"]["no_conflict"]["_draws"]
    gap_point = (
        out["by_conflict"]["no_conflict"]["accuracy"] - out["by_conflict"]["conflict"]["accuracy"]
    )
    out["gap"] = {"points": round(gap_point, 4), "ci95": ci(non - conf), "_draws": non - conf}
    errors = sum(r["error"] is not None for r in rows)
    out["errors"] = errors
    out["unparseable"] = sum(not r["parse_ok"] for r in rows)
    return out


def capture(boot: SceneBoot, rows: list[dict], families: tuple[str, ...] | None = None) -> dict:
    def m(r: dict) -> bool:
        return r["shortcut"] is not None and (families is None or r["family"] in families)

    misses = boot.sums(rows, lambda r: 1, m)
    hits = boot.sums(rows, lambda r: r["shortcut"], m)
    chance = boot.sums(rows, lambda r: r["chance"], m)
    share, share_d = boot.ratio(hits, misses)
    ch, ch_d = boot.ratio(chance, misses)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio_d = share_d / ch_d
    conflict_tasks = sum(
        1 for r in rows if r["conflict"] is True and (families is None or r["family"] in families)
    )
    unparse = sum(
        1
        for r in rows
        if r["conflict"] is True
        and not r["parse_ok"]
        and (families is None or r["family"] in families)
    )
    return {
        "conflict_tasks": conflict_tasks,
        "answered_misses": int(misses.sum()),
        "unparseable_on_conflict": unparse,
        "shortcut_picks": int(hits.sum()),
        "capture_share": None if share is None else round(float(share), 4),
        "capture_ci95": ci(share_d),
        "chance_share": None if ch is None else round(float(ch), 4),
        "chance_ci95": ci(ch_d),
        "ratio": None if not share or not ch else round(float(share / ch), 3),
        "ratio_ci95": ci(ratio_d),
    }


def matched(boot: SceneBoot, rows: list[dict]) -> dict[str, Any]:
    """Conflict minus non-conflict accuracy inside each family x camera stratum, averaged with
    the stratum's conflict-task count as weight."""
    spatial = [r for r in rows if r["conflict"] is not None]
    strata = sorted({(r["family"], r["camera"]) for r in spatial})
    S, K = len(boot.scenes), len(strata)
    idx = {s: i for i, s in enumerate(strata)}
    cc, cn, nc, nn = (np.zeros((S, K)) for _ in range(4))
    for r in spatial:
        i, k = boot.index[r["cluster"]], idx[(r["family"], r["camera"])]
        if r["conflict"]:
            cn[i, k] += 1
            cc[i, k] += r["correct"]
        else:
            nn[i, k] += 1
            nc[i, k] += r["correct"]

    def stat(CC, CN, NC, NN):
        with np.errstate(invalid="ignore", divide="ignore"):
            d = CC / CN - NC / NN
        ok = (CN > 0) & (NN > 0)
        w = np.where(ok, CN, 0.0)
        d = np.where(ok, d, 0.0)
        return (w * d).sum(axis=-1) / w.sum(axis=-1)

    point = float(stat(cc.sum(0), cn.sum(0), nc.sum(0), nn.sum(0)))
    W = boot.w
    draws = stat(W @ cc, W @ cn, W @ nc, W @ nn)
    per = {}
    for (f, c), k in idx.items():
        a, b, x, y = cc[:, k].sum(), cn[:, k].sum(), nc[:, k].sum(), nn[:, k].sum()
        if b and y:
            per[f"{f}|{c}"] = {
                "conflict": {"n": int(b), "accuracy": round(a / b, 4)},
                "no_conflict": {"n": int(y), "accuracy": round(x / y, 4)},
                "diff": round(a / b - x / y, 4),
            }
    used = sum(v["conflict"]["n"] for v in per.values())
    return {
        "gap_conflict_minus_no_conflict": round(point, 4),
        "ci95": ci(draws),
        "strata_used": len(per),
        "conflict_tasks_in_matched_strata": used,
        "strata": per,
        "_draws": draws,
    }


# --- logistic regression ----------------------------------------------------------------------


def irls(X: np.ndarray, y: np.ndarray, max_iter: int = 100, tol: float = 1e-9):
    beta = np.zeros(X.shape[1])
    converged = False
    it = 0
    for step_no in range(1, max_iter + 1):
        it = step_no
        p = 1 / (1 + np.exp(-np.clip(X @ beta, -30, 30)))
        w = p * (1 - p)
        H = X.T @ (X * w[:, None])
        g = X.T @ (y - p)
        try:
            step = np.linalg.solve(H + 1e-10 * np.eye(len(beta)), g)
        except np.linalg.LinAlgError:
            break
        beta = beta + step
        if np.max(np.abs(step)) < tol:
            converged = True
            break
    p = 1 / (1 + np.exp(-np.clip(X @ beta, -30, 30)))
    H = X.T @ (X * (p * (1 - p))[:, None])
    return beta, p, H, converged, it


def design(rows: list[dict], tasks: dict[str, dict], extra: dict[str, list[str]] | None = None):
    """X for spatial tasks with every covariate present; columns named."""
    spatial = [r for r in rows if r["conflict"] is not None]
    keep = []
    for r in spatial:
        info = tasks[r["task_id"]]
        if None in (info["visible"], info["g4_margin"], info["n_objects"], info["quality"]):
            continue
        keep.append(r)
    fams = sorted({r["family"] for r in keep})
    num = np.array(
        [
            [
                tasks[r["task_id"]]["visible"],
                tasks[r["task_id"]]["g4_margin"],
                tasks[r["task_id"]]["n_objects"],
                tasks[r["task_id"]]["quality"],
            ]
            for r in keep
        ],
        dtype=float,
    )
    mu, sd = num.mean(0), num.std(0)
    sd[sd == 0] = 1
    z = (num - mu) / sd
    cols = [
        "intercept",
        "conflict",
        "visible_fraction_z",
        "g4_margin_z",
        "object_count_z",
        "camera_quality_z",
    ] + [f"family={f}" for f in fams[1:]]
    X = [
        [1.0, float(r["conflict"]), *z[i], *[float(r["family"] == f) for f in fams[1:]]]
        for i, r in enumerate(keep)
    ]
    if extra:
        for name, levels in extra.items():
            cols += [f"{name}={lv}" for lv in levels[1:]]
            for i, r in enumerate(keep):
                X[i] += [float(r[name] == lv) for lv in levels[1:]]
    X = np.array(X)
    y = np.array([float(r["correct"]) for r in keep])
    groups = [r["cluster"] for r in keep]
    return X, y, groups, cols, keep, {"mean": mu.tolist(), "sd": sd.tolist()}


def logit_report(rows: list[dict], tasks: dict, boot_seed: int, extra=None) -> dict[str, Any]:
    X, y, groups, cols, keep, scale = design(rows, tasks, extra)
    beta, p, H, converged, iters = irls(X, y)
    try:
        bread = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        return {"stable": False, "reason": "singular information matrix", "n": len(y)}
    resid = (y - p)[:, None] * X
    by: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(X.shape[1]))
    for g, s in zip(groups, resid, strict=True):
        by[g] = by[g] + s
    G = len(by)
    meat = sum(np.outer(s, s) for s in by.values())
    V = bread @ meat @ bread * G / max(1, G - 1)
    se = np.sqrt(np.clip(np.diag(V), 0, None))
    j = cols.index("conflict")
    cond = float(np.linalg.cond(H))
    reasons = []
    if not converged:
        reasons.append("did not converge")
    if np.any(np.abs(beta) > 15):
        reasons.append("a coefficient above 15 (separation)")
    if cond > 1e10:
        reasons.append(f"ill-conditioned (cond {cond:.1e})")
    if not np.all(np.isfinite(se)):
        reasons.append("non-finite standard errors")
    # scene bootstrap of the conflict coefficient
    rng = np.random.default_rng(boot_seed)
    scene_rows: dict[str, list[int]] = defaultdict(list)
    for i, g in enumerate(groups):
        scene_rows[g].append(i)
    scenes = list(scene_rows)
    bb = []
    fails = 0
    for _ in range(B_LOGIT):
        pick = rng.integers(0, len(scenes), len(scenes))
        idx = np.concatenate([scene_rows[scenes[k]] for k in pick])
        b2, _, _, conv2, _ = irls(X[idx], y[idx], max_iter=60)
        if conv2 and np.all(np.abs(b2) < 15):
            bb.append(b2[j])
        else:
            fails += 1
    bb = np.array(bb)
    if fails > 0.05 * B_LOGIT:
        reasons.append(f"{fails} of {B_LOGIT} bootstrap fits failed")
    b, s = float(beta[j]), float(se[j])
    return {
        "n": int(len(y)),
        "scenes": G,
        "columns": cols,
        "coef": [round(float(v), 4) for v in beta],
        "se_cluster": [round(float(v), 4) for v in se],
        "conflict_log_odds": round(b, 4),
        "conflict_ci95_cluster": [round(b - 1.96 * s, 4), round(b + 1.96 * s, 4)],
        "conflict_odds_ratio": round(math.exp(b), 3),
        "conflict_or_ci95_cluster": [
            round(math.exp(b - 1.96 * s), 3),
            round(math.exp(b + 1.96 * s), 3),
        ],
        "conflict_ci95_bootstrap": ci(bb),
        "conflict_or_ci95_bootstrap": None
        if ci(bb) is None
        else [round(math.exp(v), 3) for v in ci(bb)],
        "bootstrap_fits": int(len(bb)),
        "converged": converged,
        "iterations": iters,
        "condition_number": round(cond, 1),
        "stable": not reasons,
        "unstable_reasons": reasons,
        "scaling": scale,
    }


# --- chart ------------------------------------------------------------------------------------


def chart(models: list[dict], out_png: Path, n_tasks: int) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    blue, orange = "#2a78d6", "#eb6834"  # validated pair (CVD dE 24.7, normal 33.6)
    ink, muted, grid = "#0b0b0b", "#52514e", "#e4e3df"
    fig, ax = plt.subplots(figsize=(10, 5.6), dpi=200)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")
    xs = np.arange(len(models))
    off = 0.14
    for i, m in enumerate(models):
        for dx, lab, col, mk, name in (
            (-off, "no_conflict", blue, "o", "2D shortcut agrees"),
            (off, "conflict", orange, "^", "2D shortcut conflicts"),
        ):
            s = m["acc"]["by_conflict"][lab]
            y = s["accuracy"] * 100
            lo, hi = (v * 100 for v in s["ci95"])
            ax.plot([xs[i] + dx] * 2, [lo, hi], color=col, lw=2, solid_capstyle="round", zorder=2)
            ax.scatter(
                xs[i] + dx,
                y,
                s=90,
                marker=mk,
                color=col,
                edgecolor="#fcfcfb",
                linewidth=2,
                zorder=3,
                label=name if i == 0 else None,
            )
            ax.annotate(
                f"{y:.0f}%",
                (xs[i] + dx, hi),
                textcoords="offset points",
                xytext=(0, 6),
                ha="center",
                fontsize=10,
                color=ink,
            )
    ax.set_xticks(
        xs, [f"{m['label']}\neffort {m['effort']}" for m in models], fontsize=11, color=ink
    )
    ax.set_ylim(0, 105)
    ax.set_yticks(range(0, 101, 20), [f"{v}%" for v in range(0, 101, 20)], fontsize=10, color=muted)
    ax.set_ylabel("Accuracy", fontsize=11, color=muted)
    ax.grid(axis="y", color=grid, lw=1)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(grid)
    ax.tick_params(axis="both", length=0)
    all_lower = all(m["acc"]["gap"]["ci95"] and m["acc"]["gap"]["ci95"][0] > 0 for m in models)
    title = (
        "Every Claude model is less accurate when the 2D shortcut conflicts with the 3D truth"
        if all_lower
        else "Accuracy when the 2D shortcut agrees with, or conflicts with, the 3D truth"
    )
    fig.text(0.02, 0.965, title, ha="left", va="top", fontsize=14, color=ink, fontweight="bold")
    fig.text(
        0.02,
        0.915,
        f"Spatial questions in frozen dataset v1, the same {n_tasks} tasks for every model. "
        "Bars are 95% intervals from a bootstrap over scenes.",
        ha="left",
        va="top",
        fontsize=10,
        color=muted,
    )
    ax.set_xlim(-0.6, len(models) - 0.4)
    leg = ax.legend(
        loc="lower left",
        bbox_to_anchor=(0, 1.0),
        ncol=2,
        frameon=False,
        fontsize=10,
        labelcolor=ink,
        handletextpad=0.3,
        columnspacing=1.6,
    )
    for h in leg.legend_handles:
        h.set_sizes([70])
    fig.tight_layout(rect=(0.0, 0, 1, 0.9))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, facecolor=fig.get_facecolor())
    fig.savefig(out_png.with_suffix(".svg"), facecolor=fig.get_facecolor())
    plt.close(fig)


# --- markdown ---------------------------------------------------------------------------------


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def ci_txt(c: list[float] | None, scale: float = 100, unit: str = "%") -> str:
    if not c:
        return ""
    return f"{c[0] * scale:.0f} to {c[1] * scale:.0f}{unit}"


def stat_cell(s: dict) -> str:
    if not s or s.get("n", 0) == 0:
        return "–"
    return f"{pct(s['accuracy'])} ({ci_txt(s['ci95'])}; n {s['n']})"


def pts(x: float) -> str:
    return f"{x * 100:.0f}"


def rng(c: list[float] | None, signed: bool = False) -> str:
    if not c:
        return ""
    f = "{:+.0f}" if signed else "{:.0f}"
    return f" (95% CI {f.format(c[0] * 100)} to {f.format(c[1] * 100)})"


def survives(m: dict) -> bool:
    lg, mt = m["logit"], m["matched"]
    matched_ok = bool(mt["ci95"]) and mt["ci95"][1] < 0
    logit_ok = (not lg.get("stable")) or (lg["conflict_or_ci95_cluster"][1] < 1)
    return matched_ok and logit_ok


def verdicts(out: dict[str, Any]) -> list[str]:
    """Plain sentences for the report and the morning summary, computed from the numbers."""
    lines = []
    for m in out["models"].values():
        name = f"{m['label']} at effort {m['effort']}"
        bc = m["acc"]["by_conflict"]
        g = m["acc"]["gap"]
        lines.append(
            f"{name}: {pts(bc['no_conflict']['accuracy'])}% when the 2D shortcut agrees with the "
            f"3D truth, {pts(bc['conflict']['accuracy'])}% when it conflicts; gap "
            f"{pts(g['points'])} points{rng(g['ci95'])}."
        )

    def ratio_txt(c: dict) -> str:
        r = c["ratio_ci95"]
        return f"{c['ratio']:.2f}x" + (f", 95% CI {r[0]:.2f} to {r[1]:.2f}" if r else "")

    parts = [
        f"{m['label']} {m['effort']} {pts(c['capture_share'])}% vs {pts(c['chance_share'])}% "
        f"({ratio_txt(c)})"
        for m in out["models"].values()
        if (c := m["capture"])["capture_share"] is not None
    ]
    if parts:
        lines.append(
            "Shortcut capture: of the conflict tasks each model got wrong, the share answered "
            "with exactly the 2D-shortcut answer, against chance: " + "; ".join(parts) + "."
        )
    near = [
        f"{m['label']} {m['effort']} {c['shortcut_picks']} of {c['answered_misses']} "
        f"(chance {pts(c['chance_share'])}%)"
        for m in out["models"].values()
        if (c := m["capture_by_family"].get("nearest_to_gripper")) and c["answered_misses"]
    ]
    if near:
        others = []
        for f in ("relation", "occlusion", "reachability"):
            picks = misses = 0
            chance = 0.0
            for m in out["models"].values():
                c = m["capture_by_family"].get(f)
                if c and c["answered_misses"]:
                    picks += c["shortcut_picks"]
                    misses += c["answered_misses"]
                    chance += c["chance_share"] * c["answered_misses"]
            if misses:
                others.append(f"{fam(f)} {picks} of {misses} (chance {chance / misses * 100:.0f}%)")
        lines.append(
            "The pull is strongest for 'nearest to the gripper', where wrong answers name the "
            "object nearest in the image: " + "; ".join(near) + ". Other object families, all "
            "models pooled: " + "; ".join(others) + "."
        )
    ok = [m for m in out["models"].values() if survives(m)]
    allm = list(out["models"].values())
    if allm:
        word = "every model" if len(ok) == len(allm) else f"{len(ok)} of {len(allm)} models"
        mt = ", ".join(
            f"{m['label']} {m['effort']} "
            f"{m['matched']['gap_conflict_minus_no_conflict'] * 100:+.0f}"
            for m in allm
        )
        lines.append(
            f"Confound check: the gap survives for {word}. Within the same family and camera, "
            f"conflict tasks score lower by (points): {mt}; the logistic regression holding "
            "visible fraction, G4 margin, object count, camera quality and family fixed gives "
            "conflict odds ratios of "
            + ", ".join(
                f"{m['label']} {m['effort']} {m['logit'].get('conflict_odds_ratio')}"
                + (
                    f" ({m['logit']['conflict_or_ci95_cluster'][0]} to "
                    f"{m['logit']['conflict_or_ci95_cluster'][1]})"
                    if m["logit"].get("stable")
                    else " (unstable)"
                )
                for m in allm
            )
            + "."
        )
    comp = out.get("comparisons", {}).get("claude-opus-5-5-xhigh vs claude-opus-5-5")
    if comp:
        lines.append(
            "More thinking (Opus 5.5 xhigh vs high): the gap changes by "
            f"{comp['gap_change_points'] * 100:+.0f} points{rng(comp['gap_change_ci95'], True)}; "
            f"accuracy on conflict tasks by {comp['conflict_accuracy_change'] * 100:+.0f} points"
            f"{rng(comp['conflict_accuracy_change_ci95'], True)}."
        )
    return lines


FAMILY_NAMES = {
    "nearest_to_gripper": "nearest to gripper",
    "front_to_back": "front to back",
    "free_space": "free space",
    "count_attribute": "count by colour",
    "gripper_view": "gripper view",
}


def fam(f: str) -> str:
    return FAMILY_NAMES.get(f, f)


SCALE_STATED = Path(__file__).resolve().parents[1] / "docs/reports/v1/scale_stated.json"


def scale_section(nxt: int) -> tuple[list[str], int]:
    """Brief VTF-08: distance and free space with the scale stated (scripts/scale_findings.py)."""
    if not SCALE_STATED.is_file():
        return [], nxt
    data = json.loads(SCALE_STATED.read_text())

    def pp(e: dict) -> str:
        c = e["stated_minus_not_ci95"]
        rng = f" ({c[0] * 100:+.0f} to {c[1] * 100:+.0f})" if c else ""
        return f"{e['stated_minus_not'] * 100:+.0f}{rng}"

    parts = []
    for m in data["models"].values():
        s = m["splits"]
        a, b = s["all"]["not_stated"]["accuracy"], s["all"]["stated"]["accuracy"]
        by = f"distance {pp(s['distance'])}, free space {pp(s['free_space'])}"
        parts.append(f"{m['label']} {a * 100:.0f}% to {b * 100:.0f}%, {pp(s['all'])} points ({by})")
    return [
        "",
        f"## {nxt}. Scale stated",
        "",
        "v1's distance and free-space questions never said what scale the image shows. The "
        f"{data['tasks']} such tasks among the 1,567 eval and 150 audit tasks were asked again "
        "with one sentence in front: the 1 cm grid with bold lines every 10 cm, or, on scenes "
        "without the grid, the reference object's exact size. Paired, same tasks, 95% scene "
        "intervals: " + "; ".join(parts) + ". Stating the scale helps Sonnet and Haiku on "
        "distance; Opus at either effort already reads the scale; free space does not move. "
        "Details: [scale_stated.md](scale_stated.md). From programs 0.3.0 every new distance "
        "and free-space question carries the sentence; frozen v1 is unchanged.",
    ], nxt + 1


def markdown(out: dict[str, Any], run: Path, chart_rel: str) -> str:
    ms = list(out["models"].values())
    names = [f"{m['label']} {m['effort']}" for m in ms]
    costs_path = run / "sweep" / "costs.json"
    costs = json.loads(costs_path.read_text())["models"] if costs_path.is_file() else {}
    L: list[str] = []
    L += [
        "# Findings on frozen dataset v1: do Claude models take the 2D shortcut?",
        "",
        f"Generated by `scripts/shortcut_findings.py` from the model sweep of brief 06. Every "
        f"model answered the same {out['tasks_common']} tasks (the 1,500-task evaluation sample "
        f"plus the 150 blind-audit tasks) from frozen v1 (300 scenes, 3,181 tasks), with only "
        "the image and the question, through the Anthropic API: same prompts, same JSON schema, "
        "same scorer; only the model ID and effort differ. "
        f"{out['spatial_common']} of the tasks are spatial questions that carry the "
        "shortcut_conflict flag. Intervals are 95%, from a bootstrap that resamples whole "
        f"scenes ({out['scenes']} scenes; a G6 twin counts with the scene it was made from; "
        f"{out['bootstrap']['resamples']} resamples), because tasks from one scene are "
        "correlated.",
        "",
        "## Headline",
        "",
        *[f"- {v}" for v in out["verdicts"]],
        *[f"- {v}" for v in out.get("verdicts_robustness", [])],
        "",
        f"![Accuracy on conflict vs non-conflict tasks per model]({chart_rel})",
        "",
        "## 1. Accuracy",
        "",
        "| Model · effort | Overall | Shortcut agrees | Shortcut conflicts | Non-spatial | "
        "Front | Overhead | Wrist |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    def cell(st: dict | None) -> str:
        if not st or not st.get("n"):
            return "–"
        c = st.get("ci95")
        r = f" ({c[0] * 100:.0f}–{c[1] * 100:.0f})" if c else ""
        return f"{st['accuracy'] * 100:.1f}%{r}"

    for n, m in zip(names, ms, strict=True):
        a = m["acc"]
        bc, cam = a["by_conflict"], a["by_camera"]
        L.append(
            f"| {n} | {cell(a['overall'])} | {cell(bc['no_conflict'])} | {cell(bc['conflict'])} "
            f"| {cell(bc['n/a'])} | {cell(cam.get('front'))} | {cell(cam.get('overhead'))} "
            f"| {cell(cam.get('gripper'))} |"
        )
    n_counts = ms[0]["acc"]["by_conflict"] if ms else {}
    if n_counts:
        L += [
            "",
            f"Tasks per model: {n_counts['no_conflict']['n']} where the shortcut agrees, "
            f"{n_counts['conflict']['n']} where it conflicts, {n_counts['n/a']['n']} non-spatial "
            "(counts, colours, distances, support, containment, gripper view).",
        ]
    fams = sorted({f for m in ms for f in m["acc"]["by_family"]})
    L += [
        "",
        "By family:",
        "",
        "| Family | " + " | ".join(names) + " |",
        "| --- | " + " | ".join("---" for _ in names) + " |",
    ]
    for f in fams:
        L.append(
            f"| {fam(f)} | " + " | ".join(cell(m["acc"]["by_family"].get(f)) for m in ms) + " |"
        )
    sp = sorted({k.split("|")[0] for m in ms for k in m["acc"]["by_family_conflict"]})
    L += [
        "",
        "Spatial families, shortcut agrees / conflicts:",
        "",
        "| Family | " + " | ".join(names) + " |",
        "| --- | " + " | ".join("---" for _ in names) + " |",
    ]
    for f in sp:
        row = []
        for m in ms:
            a = m["acc"]["by_family_conflict"].get(f"{f}|no_conflict")
            b = m["acc"]["by_family_conflict"].get(f"{f}|conflict")
            row.append(
                f"{a['accuracy'] * 100:.0f}% / {b['accuracy'] * 100:.0f}% (n {a['n']} / {b['n']})"
                if a and b and a["n"] and b["n"]
                else "–"
            )
        L.append(f"| {fam(f)} | " + " | ".join(row) + " |")

    L += [
        "",
        "## 2. Shortcut capture vs chance",
        "",
        "Among conflict tasks a model got wrong with a readable answer, the share whose answer "
        "is exactly the 2D-shortcut answer, against the chance share (per task, 1 divided by "
        "the number of wrong answers on offer, averaged). Yes/no and two-object questions offer "
        "one wrong answer, so every miss there is the shortcut and the ratio is 1 by "
        "construction; the object-choice families (nearest to gripper, relation, occlusion) "
        "are the informative test.",
        "",
        "| Model · effort | Scope | Conflict tasks | Answered misses | Shortcut picks | "
        "Share | Chance | Ratio (95% CI) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    def cap_row(n: str, scope: str, c: dict) -> str:
        if not c["answered_misses"]:
            return f"| {n} | {scope} | {c['conflict_tasks']} | 0 | – | – | – | – |"
        r = c["ratio_ci95"]
        rr = f"{c['ratio']}" + (f" ({r[0]:.2f}–{r[1]:.2f})" if r else "")
        return (
            f"| {n} | {scope} | {c['conflict_tasks']} | {c['answered_misses']} | "
            f"{c['shortcut_picks']} | {c['capture_share'] * 100:.0f}% | "
            f"{c['chance_share'] * 100:.0f}% | {rr} |"
        )

    for n, m in zip(names, ms, strict=True):
        L.append(cap_row(n, "all spatial", m["capture"]))
        L.append(cap_row(n, "object choice", m["capture_object_choice"]))
    L += [
        "",
        "By family (all models):",
        "",
        "| Model · effort | " + " | ".join(fam(f) for f in sp) + " |",
        "| --- | " + " | ".join("---" for _ in sp) + " |",
    ]
    for n, m in zip(names, ms, strict=True):
        cells = []
        for f in sp:
            c = m["capture_by_family"].get(f)
            if not c or not c["answered_misses"]:
                cells.append("–")
            else:
                cells.append(
                    f"{c['shortcut_picks']}/{c['answered_misses']} vs "
                    f"{c['chance_share'] * 100:.0f}% chance"
                )
        L.append(f"| {n} | " + " | ".join(cells) + " |")

    L += [
        "",
        "## 3. Confound check",
        "",
        "Conflict tasks might simply be harder in other ways (more occluded, closer margins, "
        "busier scenes, worse cameras, harder families). Two checks:",
        "",
        "- **Matched comparison:** conflict minus non-conflict accuracy inside each "
        "family x camera stratum that has both, averaged with each stratum's conflict-task "
        "count as weight.",
        "- **Logistic regression** of correct on conflict, visible fraction, G4 margin (log of "
        "the margin over the gate threshold), object count, camera quality level (clean, "
        "mild, harsh, CCTV) and family, on the spatial tasks, with scene-clustered standard "
        "errors and a 500-fit scene bootstrap as a check. A second fit adds the camera.",
        "",
        "| Model · effort | Matched gap, points (95% CI) | Strata | Odds ratio for conflict "
        "(clustered 95% CI) | Bootstrap 95% CI | With camera too | Stable | Survives |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for n, m in zip(names, ms, strict=True):
        mt, lg, lc = m["matched"], m["logit"], m["logit_with_camera"]
        c = mt["ci95"]
        mg = f"{mt['gap_conflict_minus_no_conflict'] * 100:+.1f}" + (
            f" ({c[0] * 100:+.0f} to {c[1] * 100:+.0f})" if c else ""
        )
        orc = lg.get("conflict_or_ci95_cluster") or [None, None]
        orb = lg.get("conflict_or_ci95_bootstrap") or [None, None]
        occ = lc.get("conflict_or_ci95_cluster") or [None, None]
        stable = "yes" if lg.get("stable") else "no: " + "; ".join(lg.get("unstable_reasons", []))
        L.append(
            f"| {n} | {mg} | {mt['strata_used']} | {lg.get('conflict_odds_ratio')} "
            f"({orc[0]}–{orc[1]}) | {orb[0]}–{orb[1]} | {lc.get('conflict_odds_ratio')} "
            f"({occ[0]}–{occ[1]}) | {stable} "
            f"| {'yes' if out['survives'].get(m['key']) else 'no'} |"
        )
    pooled = out.get("logit_pooled")
    if pooled and pooled.get("columns"):
        L += [
            "",
            f"Pooled fit over all models (model indicators added, n {pooled['n']} answers, "
            f"{pooled['scenes']} scenes): conflict odds ratio {pooled['conflict_odds_ratio']} "
            f"(clustered 95% CI {pooled['conflict_or_ci95_cluster'][0]} to "
            f"{pooled['conflict_or_ci95_cluster'][1]}). Coefficients (log-odds; continuous "
            "covariates standardised):",
            "",
            "| Term | Coefficient | Clustered SE |",
            "| --- | --- | --- |",
        ]
        for col, b, se in zip(pooled["columns"], pooled["coef"], pooled["se_cluster"], strict=True):
            L.append(f"| {col} | {b:+.3f} | {se:.3f} |")

    L += ["", "## 4. Does thinking help?", ""]
    comps = out.get("comparisons", {})
    if comps:
        L += [
            "Differences use the same scene resamples for both models, so they are paired. "
            "Gap = accuracy where the shortcut agrees minus where it conflicts.",
            "",
            "| Comparison | Gap change, points | Conflict accuracy change | Overall change |",
            "| --- | --- | --- | --- |",
        ]
        for k, c in comps.items():
            a, b = k.split(" vs ")
            lab = {m["key"]: f"{m['label']} {m['effort']}" for m in ms}

            def ch(v: float, ci_: list | None) -> str:
                return f"{v * 100:+.1f}" + (
                    f" ({ci_[0] * 100:+.0f} to {ci_[1] * 100:+.0f})" if ci_ else ""
                )

            L.append(
                f"| {lab.get(a, a)} vs {lab.get(b, b)} | "
                f"{ch(c['gap_change_points'], c['gap_change_ci95'])} | "
                f"{ch(c['conflict_accuracy_change'], c['conflict_accuracy_change_ci95'])} | "
                f"{ch(c['overall_change'], c['overall_change_ci95'])} |"
            )
    L += [
        "",
        "Mean thinking tokens per answer (output tokens the API reports as thinking):",
        "",
        "| Model · effort | Shortcut agrees | Shortcut conflicts | Non-spatial |",
        "| --- | --- | --- | --- |",
    ]
    for n, m in zip(names, ms, strict=True):
        t = m["mean_thinking_tokens"]

        def tk(x: dict) -> str:
            return "–" if not x or x["mean"] is None else f"{x['mean']:.0f} (n {x['n']})"

        L.append(f"| {n} | {tk(t['no_conflict'])} | {tk(t['conflict'])} | {tk(t['n/a'])} |")

    extra, nxt = robustness_sections(out)
    L += extra
    scale, nxt = scale_section(nxt)
    L += scale
    L += [
        "",
        f"## {nxt}. Human comparison",
        "",
        "Pending the founder's blind audit of the 150 audit tasks (all models answered them). "
        "When the export is in, `uv run python scripts/audit_vs_models.py --run out/v1 "
        "--answers <export.json>` scores it with the same scorer and writes "
        "human_vs_models.md beside this file.",
        "",
        "## Cost",
        "",
        "| Model · effort | Answers | Input tokens | Output tokens | of which thinking | "
        "Cost (USD) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for n, m in zip(names, ms, strict=True):
        c = costs.get(m["key"])
        if c:
            t = c["tokens"]
            L.append(
                f"| {n} | {c['replies']} | {t.get('input', 0):,} | {t.get('output', 0):,} | "
                f"{t.get('thinking', 0):,} | {c['cost_usd']:.2f} |"
            )
    L += [
        "",
        "Batched answers bill at half the list price; Sonnet's first 1,500 answers were standard "
        "calls from 23 Nov.",
        "",
        "## Method notes",
        "",
        "- Settings on every call: thinking adaptive, effort set explicitly, max_tokens 16,000 "
        "(64,000 and a 1,200 s timeout at xhigh), structured JSON output, no sampling "
        "parameters; anthropic SDK 2.12.0, API version 2026-10-21. Each result records the "
        "model ID, API and SDK version, effort, max_tokens, input, output and thinking tokens, "
        "and whether it came from a standard call or the Message Batches API (same request "
        "body).",
        "- A miss counts as a shortcut pick when the eval scorer accepts the answer against the "
        f"task's shortcut_answer. {len(out.get('shortcut_matched_by_noun', []))} relation "
        "tasks name a shortcut object that is not nameable in that view; those are matched by "
        "noun.",
        "- Unparseable answers count as wrong in accuracy and are left out of the capture "
        "share (they name nothing).",
        "- Relation tasks with a single candidate have no runner-up and no finite G4 margin; "
        "the regression gives them the largest margin observed.",
        "- The regression is fit by IRLS in numpy; it is called unstable if it fails to "
        "converge, a coefficient exceeds 15 in absolute value, or over 5% of bootstrap fits "
        "fail, in which case only the matched comparison is reported.",
        "",
    ]
    return "\n".join(L)


ROBUSTNESS_KEYS = ("breaking_point", "twin_flip", "risk_cards", "verdicts_robustness")


def robustness_sections(out: dict[str, Any]) -> tuple[list[str], int]:
    """Sections 5 on (breaking points, twins, risk cards) when scripts/robustness_findings.py
    has added them to findings.json; else nothing."""
    if not any(k in out for k in ROBUSTNESS_KEYS):
        return [], 5
    from robustness_findings import markdown_sections

    return markdown_sections(out, 5)


def gallery(models: dict[str, list[dict]], tasks: dict[str, dict], n: int = 12) -> list[dict]:
    """Conflict tasks where the most models picked the shortcut; one per scene, spread over
    families and cameras."""
    votes: dict[str, list[str]] = defaultdict(list)
    for key, rows in models.items():
        for r in rows:
            if r["shortcut"]:
                votes[r["task_id"]].append(key)
    ranked = sorted(votes.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    picked, scenes, fam_count = [], set(), Counter()
    for tid, keys in ranked:
        info = tasks[tid]
        if info["scene_id"] in scenes or fam_count[info["family"]] >= 3:
            continue
        task = info["task"]
        picked.append(
            {
                "task_id": tid,
                "scene_id": info["scene_id"],
                "family": info["family"],
                "camera": info["camera"],
                "question": task.question,
                "truth": task.answer_text,
                "shortcut_answer": task.shortcut_answer,
                "models_taking_shortcut": keys,
            }
        )
        scenes.add(info["scene_id"])
        fam_count[info["family"]] += 1
        if len(picked) >= n:
            break
    return picked


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", default="docs/reports/v1")
    ap.add_argument("--media", default="docs/media")
    args = ap.parse_args()
    run = Path(args.run).resolve()
    task_set = json.loads((run / "sweep" / "task_set.json").read_text())
    ids = task_set["task_ids"]
    tasks = load_tasks(run, ids)
    scenes = sorted({t["cluster"] for t in tasks.values()})
    boot = SceneBoot(scenes)

    rows_by: dict[str, list[dict]] = {}
    for key, _, _ in MODELS:
        rows = load_model(run, key, tasks)
        if rows:
            rows_by[key] = rows
    # common task set: the tasks every present model answered in the sweep files
    common = set.intersection(*(set(r["task_id"] for r in rs) for rs in rows_by.values()))
    out: dict[str, Any] = {
        "run": f"out/{run.name}",
        "tasks_in_set": len(ids),
        "tasks_common": len(common),
        "scenes": len(scenes),
        "bootstrap": {"resamples": B, "unit": "scene", "seed": SEED},
        "models": {},
    }
    summaries = []
    for key, label, effort in MODELS:
        if key not in rows_by:
            continue
        rows = [r for r in rows_by[key] if r["task_id"] in common]
        acc = accuracy_table(boot, rows)
        cap = capture(boot, rows)
        cap_obj = capture(boot, rows, OBJECT_CHOICE)
        cap_fam = {
            f: capture(boot, rows, (f,))
            for f in sorted({r["family"] for r in rows if r["conflict"]})
        }
        mt = matched(boot, rows)
        lg = logit_report(rows, tasks, SEED + len(summaries))
        lg_cam = logit_report(
            rows,
            tasks,
            SEED + 100 + len(summaries),
            extra={"camera": sorted({r["camera"] for r in rows})},
        )
        thinking = {}
        for lab in ("conflict", "no_conflict", "n/a"):
            vals = [
                r["thinking"]
                for r in rows
                if conflict_label(r["conflict"]) == lab and r["thinking"] is not None
            ]
            thinking[lab] = {
                "n": len(vals),
                "mean": round(float(np.mean(vals)), 1) if vals else None,
                "median": float(np.median(vals)) if vals else None,
            }
        m = {
            "key": key,
            "label": label,
            "effort": effort,
            "acc": acc,
            "capture": cap,
            "capture_object_choice": cap_obj,
            "capture_by_family": cap_fam,
            "matched": mt,
            "logit": lg,
            "logit_with_camera": lg_cam,
            "mean_thinking_tokens": thinking,
        }
        summaries.append(m)
        out["models"][key] = m

    # does thinking help / gaps across models (paired over the same scene draws)
    comps = {}
    by_key = {m["key"]: m for m in summaries}
    pairs = [
        ("claude-opus-5-5-xhigh", "claude-opus-5-5"),
        ("claude-opus-5-5", "claude-sonnet-5-5"),
        ("claude-sonnet-5-5", "claude-haiku-5-5"),
        ("claude-opus-5-5", "claude-haiku-5-5"),
    ]
    for a, b in pairs:
        if a in by_key and b in by_key:
            da, db = by_key[a]["acc"]["gap"], by_key[b]["acc"]["gap"]
            oa, ob = by_key[a]["acc"]["overall"], by_key[b]["acc"]["overall"]
            ca = by_key[a]["acc"]["by_conflict"]["conflict"]
            cb = by_key[b]["acc"]["by_conflict"]["conflict"]
            comps[f"{a} vs {b}"] = {
                "gap_change_points": round(da["points"] - db["points"], 4),
                "gap_change_ci95": ci(da["_draws"] - db["_draws"]),
                "overall_change": round(oa["accuracy"] - ob["accuracy"], 4),
                "overall_change_ci95": ci(oa["_draws"] - ob["_draws"]),
                "conflict_accuracy_change": round(ca["accuracy"] - cb["accuracy"], 4),
                "conflict_accuracy_change_ci95": ci(ca["_draws"] - cb["_draws"]),
            }
    out["comparisons"] = comps
    # pooled regression with model indicators
    pooled_rows = []
    for m in summaries:
        for r in rows_by[m["key"]]:
            if r["task_id"] in common:
                pooled_rows.append({**r, "model": m["key"]})
    if pooled_rows:
        out["logit_pooled"] = logit_report(
            pooled_rows, tasks, SEED + 999, extra={"model": [m["key"] for m in summaries]}
        )
    out["shortcut_matched_by_noun"] = sorted(APPROX)
    out["spatial_common"] = sum(1 for t in common if tasks[t]["conflict"] is not None)
    out["verdicts"] = verdicts(out)
    out["survives"] = {m["key"]: survives(m) for m in summaries}
    out["gallery"] = gallery({k: v for k, v in rows_by.items()}, tasks)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    old = out_dir / "findings.json"
    if old.is_file():  # keep what scripts/robustness_findings.py added
        prev = json.loads(old.read_text())
        out.update({k: prev[k] for k in ROBUSTNESS_KEYS if k in prev})
    (out_dir / "findings.json").write_text(json.dumps(strip(out), indent=2) + "\n")
    chart_png = Path(args.media) / "shortcut_conflict_accuracy.png"
    try:
        chart_rel = str(Path(os.path.relpath(chart_png.resolve(), out_dir.resolve())))
    except ValueError:
        chart_rel = str(chart_png)
    (out_dir / "findings.md").write_text(markdown(strip(out), run, chart_rel))
    n_spatial = sum(1 for t in common if tasks[t]["conflict"] is not None)
    chart(summaries, Path(args.media) / "shortcut_conflict_accuracy.png", n_spatial)
    print(json.dumps(strip({k: v for k, v in out.items() if k != "models"}), indent=1)[:3000])
    for m in summaries:
        a = m["acc"]
        print(
            f"{m['key']}: overall {stat_cell(a['overall'])}; conflict "
            f"{stat_cell(a['by_conflict']['conflict'])}; no conflict "
            f"{stat_cell(a['by_conflict']['no_conflict'])}; gap {a['gap']['points']:.3f} "
            f"{a['gap']['ci95']}; capture {m['capture']['capture_share']} vs chance "
            f"{m['capture']['chance_share']} ratio {m['capture']['ratio']} "
            f"{m['capture']['ratio_ci95']}; "
            f"matched {m['matched']['gap_conflict_minus_no_conflict']} {m['matched']['ci95']}; "
            f"logit OR {m['logit'].get('conflict_odds_ratio')} "
            f"{m['logit'].get('conflict_or_ci95_cluster')} "
            f"stable {m['logit'].get('stable')}"
        )


if __name__ == "__main__":
    main()
