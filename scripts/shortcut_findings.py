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
    the view (minus the anchor or occluder, which cannot be the answer); set questions the sets
    one object away from the truth (one per object)."""
    task = info["task"]
    k = len(task.scene_objects or [])
    fam = info["family"]
    if task.answer_type == "bool" or fam == "front_to_back":
        return 1
    if fam == "nearest_to_gripper":
        return max(1, k - 1)
    if fam == "relation":
        n = info.get("n_candidates")
        return max(1, (n - 1) if n else k - 2)
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
                out[self.index[r["scene_id"]]] += float(value(r))
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
        i, k = boot.index[r["scene_id"]], idx[(r["family"], r["camera"])]
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
    groups = [r["scene_id"] for r in keep]
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
    fig.tight_layout(rect=(0.0, 0, 1, 0.86))
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
    scenes = sorted({t["scene_id"] for t in tasks.values()})
    boot = SceneBoot(scenes)

    rows_by: dict[str, list[dict]] = {}
    for key, _, _ in MODELS:
        rows = load_model(run, key, tasks)
        if rows:
            rows_by[key] = rows
    # common task set: the tasks every present model answered in the sweep files
    common = set.intersection(*(set(r["task_id"] for r in rs) for rs in rows_by.values()))
    out: dict[str, Any] = {
        "run": str(run),
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
    out["gallery"] = gallery({k: v for k, v in rows_by.items()}, tasks)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "findings.json").write_text(json.dumps(strip(out), indent=2) + "\n")
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
