"""P17 dataset report: `vtf dataset-report <run>` writes <run>/report/index.html.

One static page (Jinja2, charts as inline SVG, no server and no external fetch) that a judge can
read on its own: gate pass rates G1 to G7, the rejection-reason histogram, coverage of family x
camera x difficulty, answer distributions per family, the shortcut_conflict share, the G5
baselines against model accuracy, G6 twin results, model accuracy by family, rig and
shortcut_conflict with 95% bootstrap intervals, the founder audit and render timings.

Every number is read from the run folder; nothing is typed by hand.
"""

import html
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from markupsafe import Markup

from vtf.core.io import read_jsonl, read_task
from vtf.core.layout import EVAL_DIR, EVAL_RESULTS, RunLayout
from vtf.core.program import FAMILIES
from vtf.core.task import EvalRecord, TaskPackage
from vtf.eval.blind import BLIND_SUFFIX
from vtf.eval.summary import bootstrap_ci, conflict_label, split_stats
from vtf.validate.gates.g5_vision_necessity import (
    BALANCE_FILE,
    BLIND_FILE,
    CAPS,
    PRIOR_FILE,
    answer_label,
    read_json,
    task_key,
)

REPORT_VERSION = "0.1.0"
GATE_IDS = ("G1", "G2", "G3", "G4", "G5", "G6", "G7")
GATE_NAMES = {
    "G1": "Truth by execution",
    "G2": "Second computation",
    "G3": "Evidence visible",
    "G4": "No near-ties",
    "G5": "Vision necessity (balance)",
    "G6": "Counterfactual twin",
    "G7": "Determinism",
}
RIG_ORDER = ("front", "overhead", "gripper")
PHASES = ("build", "settle", "render", "data_passes", "amodal")

# Chart geometry (SVG user units; the SVG scales to the column width).
W = 720
LABEL_W = 210
VALUE_W = 140
CARD_W = 420  # small multiples in a two-column grid
ROW_H = 26
BAR_H = 14
FONT = 12


def _e(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _pct(x: float | None, digits: int = 0) -> str:
    return "–" if x is None else f"{100 * x:.{digits}f}%"


# --- SVG charts -------------------------------------------------------------------------------


def hbar_chart(
    rows: list[dict[str, Any]],
    *,
    max_value: float | None = None,
    percent: bool = True,
    series: str = "s1",
    marker: float | None = None,
    marker_label: str = "",
    aria: str = "",
    width: int = W,
    label_w: int = LABEL_W,
    value_w: int = VALUE_W,
    label_max: int = 34,
) -> Markup:
    """Horizontal bars. Each row: label, value, text (right of the bar), tip (hover title).
    Narrow cards pass a smaller `width` so the text keeps its size when the SVG scales."""
    if not rows:
        return Markup('<p class="empty">No data.</p>')
    top = 22 if marker is not None else 6
    h = top + len(rows) * ROW_H + 6
    vmax = max_value if max_value is not None else max((r["value"] for r in rows), default=1) or 1
    plot_w = width - label_w - value_w
    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {h}" role="img" aria-label="{_e(aria)}">',
        f'<line class="axis" x1="{label_w}" y1="{top - 4}" x2="{label_w}" y2="{h - 4}"/>',
    ]
    for i, r in enumerate(rows):
        y = top + i * ROW_H
        bw = max(0.0, plot_w * min(r["value"], vmax) / vmax)
        cls = r.get("series", series)
        parts.append(
            f"<g><title>{_e(r.get('tip', r['label']))}</title>"
            f'<rect class="hit" x="0" y="{y}" width="{width}" height="{ROW_H}"/>'
            f'<text class="lbl" x="{label_w - 8}" y="{y + ROW_H / 2 + 4}" text-anchor="end">'
            f"{_e(_clip(str(r['label']), label_max))}</text>"
        )
        if bw > 0:
            parts.append(
                f'<rect class="bar {cls}" x="{label_w}" y="{y + (ROW_H - BAR_H) / 2}" '
                f'width="{bw:.1f}" height="{BAR_H}" rx="3"/>'
            )
        parts.append(
            f'<text class="val" x="{label_w + bw + 6:.1f}" y="{y + ROW_H / 2 + 4}">'
            f"{_e(r.get('text', _pct(r['value']) if percent else r['value']))}</text></g>"
        )
    if marker is not None:
        mx = label_w + plot_w * min(marker, vmax) / vmax
        parts.append(
            f'<line class="marker" x1="{mx:.1f}" y1="{top - 6}" x2="{mx:.1f}" y2="{h - 4}"/>'
            f'<text class="mk" x="{mx:.1f}" y="{top - 10}" text-anchor="middle">'
            f"{_e(marker_label)}</text>"
        )
    parts.append("</svg>")
    return Markup("".join(parts))


def dot_chart(rows: list[dict[str, Any]], series: list[tuple[str, str]], aria: str = "") -> Markup:
    """Accuracy dot plot on 0-100%: one row per label, one marker per series, optional 95%
    interval whiskers. rows: {label, points: {series_key: {v, lo, hi, tip}}}.
    series: [(key, css class)] in legend order."""
    if not rows:
        return Markup('<p class="empty">No data.</p>')
    top = 24
    h = top + len(rows) * ROW_H + 8
    plot_w = W - LABEL_W - 24
    x0 = LABEL_W

    def px(v: float) -> float:
        return x0 + plot_w * max(0.0, min(1.0, v))

    parts = [f'<svg class="chart" viewBox="0 0 {W} {h}" role="img" aria-label="{_e(aria)}">']
    for t in (0, 0.25, 0.5, 0.75, 1.0):
        parts.append(
            f'<line class="grid" x1="{px(t):.1f}" y1="{top - 6}" x2="{px(t):.1f}" y2="{h - 6}"/>'
            f'<text class="tick" x="{px(t):.1f}" y="{top - 10}" text-anchor="middle">'
            f"{int(t * 100)}%</text>"
        )
    offsets = {key: (j - (len(series) - 1) / 2) * 5 for j, (key, _) in enumerate(series)}
    for i, r in enumerate(rows):
        y = top + i * ROW_H + ROW_H / 2
        parts.append(
            f'<text class="lbl" x="{LABEL_W - 10}" y="{y + 4}" text-anchor="end">'
            f"{_e(r['label'])}</text>"
            f'<line class="rowline" x1="{x0}" y1="{y}" x2="{x0 + plot_w}" y2="{y}"/>'
        )
        for key, cls in series:
            p = r["points"].get(key)
            if not p or p.get("v") is None:
                continue
            yy = y + offsets[key]
            parts.append(f"<g><title>{_e(p.get('tip', ''))}</title>")
            if p.get("lo") is not None and p.get("hi") is not None:
                parts.append(
                    f'<line class="whisker {cls}" x1="{px(p["lo"]):.1f}" y1="{yy}" '
                    f'x2="{px(p["hi"]):.1f}" y2="{yy}"/>'
                )
            parts.append(
                f'<circle class="dot {cls}" cx="{px(p["v"]):.1f}" cy="{yy}" r="5"/>'
                f'<circle class="hit" cx="{px(p["v"]):.1f}" cy="{yy}" r="11"/></g>'
            )
    parts.append("</svg>")
    return Markup("".join(parts))


def histogram(
    values: list[float], bins: int = 12, unit: str = "", aria: str = "", width: int = W
) -> Markup:
    if not values:
        return Markup('<p class="empty">No data.</p>')
    lo, hi = min(values), max(values)
    if hi <= lo:
        hi = lo + 1
    step = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        counts[min(bins - 1, int((v - lo) / step))] += 1
    h, top, bottom, left = 150, 12, 28, 40
    plot_w, plot_h = width - left - 10, h - top - bottom
    cmax = max(counts) or 1
    bw = plot_w / bins
    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {h}" role="img" aria-label="{_e(aria)}">',
        f'<line class="axis" x1="{left}" y1="{top + plot_h}" x2="{width - 10}" '
        f'y2="{top + plot_h}"/>',
        f'<text class="tick" x="{left - 6}" y="{top + 8}" text-anchor="end">{cmax}</text>',
        f'<text class="tick" x="{left - 6}" y="{top + plot_h}" text-anchor="end">0</text>',
    ]
    for i, c in enumerate(counts):
        a, b = lo + i * step, lo + (i + 1) * step
        bh = plot_h * c / cmax
        x = left + i * bw
        parts.append(
            f"<g><title>{a:.1f} to {b:.1f}{unit}: {c}</title>"
            f'<rect class="hit" x="{x:.1f}" y="{top}" width="{bw:.1f}" height="{plot_h}"/>'
        )
        if c:
            parts.append(
                f'<rect class="bar s1" x="{x + 1:.1f}" y="{top + plot_h - bh:.1f}" '
                f'width="{max(1.0, bw - 2):.1f}" height="{bh:.1f}" rx="2"/>'
            )
        parts.append("</g>")
    for i, anchor in ((0, "start"), (bins // 2, "middle"), (bins, "end")):
        x = left + i * bw
        parts.append(
            f'<text class="tick" x="{x:.1f}" y="{h - 10}" text-anchor="{anchor}">'
            f"{lo + i * step:.0f}{unit}</text>"
        )
    parts.append("</svg>")
    return Markup("".join(parts))


def heat_table(
    row_labels: list[str],
    col_groups: list[tuple[str, list[str]]],
    cell: dict[tuple[str, str, str], int],
    caption: str = "",
) -> Markup:
    """Count table with a one-hue sequential fill; columns grouped (e.g. rig, then level)."""
    vmax = max(cell.values(), default=0) or 1
    out = ['<div class="scroll"><table class="heat">']
    if caption:
        out.append(f"<caption>{_e(caption)}</caption>")
    out.append("<thead><tr><th></th>")
    for group, cols in col_groups:
        out.append(f'<th colspan="{len(cols)}" class="grp">{_e(group)}</th>')
    out.append("<th>Total</th></tr><tr><th>Family</th>")
    for _, cols in col_groups:
        out.extend(f"<th>{_e(c)}</th>" for c in cols)
    out.append("<th></th></tr></thead><tbody>")
    for r in row_labels:
        out.append(f'<tr><th scope="row">{_e(r)}</th>')
        total = 0
        for group, cols in col_groups:
            for c in cols:
                v = cell.get((r, group, c), 0)
                total += v
                step = 0 if not v else 1 + min(5, int(6 * v / (vmax + 1)))
                out.append(
                    f'<td class="h{step}" title="{_e(f"{r}, {group} {c}: {v}")}">'
                    f"{v if v else '·'}</td>"
                )
        out.append(f'<td class="tot">{total}</td></tr>')
    out.append("</tbody></table></div>")
    return Markup("".join(out))


# --- data -------------------------------------------------------------------------------------


def _family_order(names: set[str]) -> list[str]:
    known = [f for f in FAMILIES if f in names]
    return known + sorted(names - set(known))


def _rig_order(names: set[str]) -> list[str]:
    return [r for r in RIG_ORDER if r in names] + sorted(names - set(RIG_ORDER))


def _difficulty_axis(
    tasks: list[TaskPackage], word: str, fallback: str | None = None
) -> tuple[str | None, dict[str, str]]:
    """Pick the difficulty key for a coverage axis and each task's level on it."""
    keys = sorted({k for t in tasks for k in t.difficulty if word in k.lower()})
    if keys:
        key = keys[0]
        return key, {t.task_id: str(t.difficulty.get(key, "n/a")) for t in tasks}
    if fallback and any(fallback in t.difficulty for t in tasks):
        values = sorted(float(t.difficulty[fallback]) for t in tasks if fallback in t.difficulty)
        a = values[len(values) // 3]
        b = values[(2 * len(values)) // 3]
        labels = {}
        for t in tasks:
            v = t.difficulty.get(fallback)
            if v is None:
                labels[t.task_id] = "n/a"
            else:
                v = float(v)
                labels[t.task_id] = (
                    f"≤{a:g}" if v <= a else (f"{a:g}–{b:g}" if v <= b else f">{b:g}")
                )
        return f"{fallback} (tertiles)", labels
    return None, {}


def _level_order(levels: set[str]) -> list[str]:
    known = ["clean", "mild", "harsh", "cctv", "low", "mid", "medium", "high", "normal", "dim"]
    first = [lv for lv in known if lv in levels]
    rest = sorted(levels - set(first), key=lambda s: (s == "n/a", s))
    return first + rest


def _load_records(path: Path) -> list[EvalRecord]:
    return [EvalRecord.model_validate(r) for r in read_jsonl(path)]


def collect(run_dir: Path) -> dict[str, Any]:
    lay = RunLayout(run_dir)
    vr = read_json(lay.validate_report) or {}
    run_info = read_json(lay.run_file) or {}
    manifest = list(read_jsonl(lay.task_manifest)) if lay.task_manifest.is_file() else []
    tasks = [read_task(lay.root / row["path"]) for row in manifest]
    rejected = list(read_jsonl(lay.rejected)) if lay.rejected.is_file() else []
    prior = read_json(lay.baselines_dir / PRIOR_FILE)
    balance = read_json(lay.baselines_dir / BALANCE_FILE)
    blind = read_json(lay.baselines_dir / BLIND_FILE)
    audit = read_json(lay.audit_dir / "score.json")
    render = read_json(lay.render_report) or {}

    models: dict[str, list[EvalRecord]] = {}
    blind_models: dict[str, list[EvalRecord]] = {}
    eval_root = lay.root / EVAL_DIR
    if eval_root.is_dir():
        for d in sorted(eval_root.iterdir()):
            res = d / EVAL_RESULTS
            if not res.is_file():
                continue
            records = _load_records(res)
            if d.name.endswith(BLIND_SUFFIX) or (records and not records[0].with_image):
                blind_models[d.name] = records
            else:
                models[d.name] = records
    return {
        "lay": lay,
        "vr": vr,
        "run_info": run_info,
        "tasks": tasks,
        "rejected": rejected,
        "prior": prior,
        "balance": balance,
        "blind": blind,
        "audit": audit,
        "render": render,
        "models": models,
        "blind_models": blind_models,
    }


# --- sections ---------------------------------------------------------------------------------


def gate_section(d: dict[str, Any]) -> dict[str, Any]:
    gates = dict(d["vr"].get("gates", {}))
    if d["balance"]:
        gates["G5"] = d["balance"]["G5"]
    rows, table = [], []
    for g in GATE_IDS:
        s = gates.get(g)
        name = f"{g} {GATE_NAMES[g]}"
        if not s or not s.get("run"):
            table.append({"gate": name, "run": 0, "pass": 0, "rate": None})
            rows.append(
                {
                    "label": name,
                    "value": 0,
                    "text": "not run",
                    "series": "muted",
                    "tip": f"{name}: not run in this run",
                }
            )
            continue
        rate = s["pass"] / s["run"]
        table.append({"gate": name, "run": s["run"], "pass": s["pass"], "rate": rate})
        rows.append(
            {
                "label": name,
                "value": rate,
                "text": f"{_pct(rate, 1)}  ({s['pass']}/{s['run']})",
                "tip": f"{name}: {s['pass']} of {s['run']} passed",
            }
        )
    return {"chart": hbar_chart(rows, max_value=1.0, aria="Gate pass rates"), "table": table}


def rejection_section(d: dict[str, Any], top: int = 20) -> dict[str, Any]:
    counts = Counter(f"{r.get('stage', '?')}: {r.get('reason', '?')}" for r in d["rejected"])
    stages = Counter(r.get("stage", "?") for r in d["rejected"])
    items = counts.most_common()
    shown = items[:top]
    rest = sum(c for _, c in items[top:])
    if rest:
        shown.append((f"{len(items) - top} other reasons", rest))
    total = sum(counts.values())
    rows = [
        {
            "label": k,
            "value": c,
            "text": f"{c}  ({_pct(c / total, 1)})",
            "tip": f"{k}: {c} rejections",
        }
        for k, c in shown
    ]
    return {
        "chart": hbar_chart(rows, percent=False, series="s2", aria="Rejection reasons"),
        "total": total,
        "stages": dict(stages.most_common()),
        "distinct": len(counts),
    }


def coverage_section(d: dict[str, Any]) -> dict[str, Any]:
    tasks = d["tasks"]
    fams = _family_order({t.family for t in tasks})
    rigs = _rig_order({t.rig for t in tasks})
    by_rig = Counter((t.family, "camera", t.rig) for t in tasks)
    matrices = [heat_table(fams, [("camera", rigs)], dict(by_rig), "Tasks by family and camera")]
    notes = []
    for word, fallback, title in (
        ("clutter", "n_objects", "clutter"),
        ("quality", None, "image quality"),
    ):
        key, level = _difficulty_axis(tasks, word, fallback)
        if key is None:
            notes.append(f"No {title} level is recorded in the task difficulty vectors yet.")
            continue
        levels = _level_order(set(level.values()))
        cell = Counter((t.family, t.rig, level[t.task_id]) for t in tasks)
        matrices.append(
            heat_table(
                fams,
                [(r, levels) for r in rigs],
                dict(cell),
                f"Tasks by family, camera and {title} ({key})",
            )
        )
    return {"matrices": matrices, "notes": notes}


def answer_section(d: dict[str, Any]) -> list[dict[str, Any]]:
    by_family: dict[str, list[TaskPackage]] = defaultdict(list)
    for t in d["tasks"]:
        by_family[t.family].append(t)
    cards = []
    caps = (d["balance"] or {}).get("families", {})
    for family in _family_order(set(by_family)):
        members = by_family[family]
        atype = members[0].answer_type
        cap = caps.get(family, {}).get("cap", CAPS.get(atype))
        if atype == "number":
            values = [float(t.answer) for t in members]  # type: ignore[arg-type]
            chart = histogram(values, bins=10, unit=" cm", aria=f"{family} answers", width=CARD_W)
            top_share = None
        else:
            counts = Counter(task_key(t) for t in members)
            items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            shown = items[:6]
            rest = sum(c for _, c in items[6:])
            rows = [
                {
                    "label": answer_label(atype, k),
                    "value": c / len(members),
                    "tip": f"{answer_label(atype, k)}: {c} of {len(members)}",
                }
                for k, c in shown
            ]
            if rest:
                rows.append(
                    {
                        "label": f"{len(items) - 6} other values",
                        "value": rest / len(members),
                        "series": "muted",
                        "tip": f"other values: {rest}",
                    }
                )
            top_share = shown[0][1] / len(members) if shown else None
            scale = min(1.0, 1.25 * max([cap or 0.0] + [r["value"] for r in rows]))
            chart = hbar_chart(
                rows,
                max_value=scale,
                marker=cap,
                marker_label=f"cap {_pct(cap)}" if cap else "",
                aria=f"{family} answer distribution",
                width=CARD_W,
                label_w=150,
                value_w=44,
                label_max=22,
            )
        cards.append(
            {
                "family": family,
                "n": len(members),
                "type": atype,
                "chart": chart,
                "top_share": top_share,
                "cap": cap,
            }
        )
    return cards


def conflict_section(d: dict[str, Any]) -> dict[str, Any]:
    by_family: dict[str, list[TaskPackage]] = defaultdict(list)
    for t in d["tasks"]:
        if t.shortcut_conflict is not None:
            by_family[t.family].append(t)
    rows = []
    for family in _family_order(set(by_family)):
        ts = by_family[family]
        k = sum(bool(t.shortcut_conflict) for t in ts)
        rows.append(
            {
                "label": family,
                "value": k / len(ts),
                "text": f"{_pct(k / len(ts))}  ({k}/{len(ts)})",
                "tip": f"{family}: the 2D shortcut gives the wrong answer on {k} of {len(ts)}",
            }
        )
    return {
        "chart": hbar_chart(rows, max_value=1.0, series="s2", aria="Shortcut conflict share"),
        "n": sum(len(v) for v in by_family.values()),
    }


def _split_points(records: list[EvalRecord], key) -> dict[str, Any]:
    return {k: s.model_dump() for k, s in split_stats(records, key).items()}


def model_section(d: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for name, records in d["models"].items():
        if not records:
            continue
        correct = sum(r.correct for r in records)
        fam = _split_points(records, lambda r: r.family)
        rig = _split_points(records, lambda r: r.rig or "unknown")
        conf = _split_points(records, lambda r: conflict_label(r.shortcut_conflict))
        fxc = _split_points(records, lambda r: f"{r.family}|{conflict_label(r.shortcut_conflict)}")
        fam_rows = [
            {
                "label": f,
                "points": {
                    "model": {
                        "v": s["accuracy"],
                        "lo": s["ci95_low"],
                        "hi": s["ci95_high"],
                        "tip": f"{f}: {s['correct']}/{s['n']} = {_pct(s['accuracy'], 1)} "
                        f"(95% CI {_pct(s['ci95_low'])} to {_pct(s['ci95_high'])})",
                    }
                },
            }
            for f, s in sorted(fam.items(), key=lambda kv: _family_order(set(fam)).index(kv[0]))
        ]
        spatial = sorted(
            {f for f in fam if f"{f}|true" in fxc or f"{f}|false" in fxc},
            key=lambda f: _family_order(set(fam)).index(f),
        )
        conflict_rows = []
        for f in spatial:
            points = {}
            for key, label in (("false", "agree"), ("true", "conflict")):
                s = fxc.get(f"{f}|{key}")
                if s:
                    points[label] = {
                        "v": s["accuracy"],
                        "lo": s["ci95_low"],
                        "hi": s["ci95_high"],
                        "tip": f"{f}, shortcut {'conflicts' if key == 'true' else 'agrees'}: "
                        f"{s['correct']}/{s['n']} = {_pct(s['accuracy'], 1)}",
                    }
            conflict_rows.append({"label": f, "points": points})
        ci = bootstrap_ci(correct, len(records))
        out.append(
            {
                "name": name,
                "n": len(records),
                "correct": correct,
                "accuracy": correct / len(records),
                "ci": ci,
                "parse_failures": sum(not r.parse_ok for r in records),
                "families": fam,
                "rigs": rig,
                "conflict": conf,
                "fxc": fxc,
                "family_chart": dot_chart(
                    fam_rows, [("model", "s1")], aria=f"{name} accuracy by family"
                ),
                "conflict_chart": dot_chart(
                    conflict_rows,
                    [("agree", "s3"), ("conflict", "s2")],
                    aria=f"{name} accuracy when the 2D shortcut agrees or conflicts",
                )
                if conflict_rows
                else None,
            }
        )
    return out


def _answer_text(prior: dict[str, Any]) -> str:
    if prior["answer_type"] == "number":
        return f"{prior['prior_answer']} (median)"
    return answer_label(prior["answer_type"], str(prior["prior_answer"]))


def g5_section(d: dict[str, Any], models: list[dict[str, Any]]) -> dict[str, Any] | None:
    prior = d["prior"]
    if not prior and not d["blind"]:
        return None
    after = (prior or {}).get("after", {}).get("families", {})
    before = (prior or {}).get("before_balance", {}).get("families", {})
    blind = (d["blind"] or {}).get("families", {})
    blind_base = ((d["blind"] or {}).get("model") or "").removesuffix(BLIND_SUFFIX)
    named = [m for m in models if m["name"] == blind_base]
    model = named[0] if named else max(models, key=lambda m: (m["n"], m["name"]), default=None)
    fams = _family_order(set(after) | set(blind) | set(model["families"] if model else {}))
    rows, table = [], []
    for f in fams:
        points: dict[str, Any] = {}
        p = after.get(f)
        if p:
            points["prior"] = {
                "v": p["accuracy"],
                "tip": f"{f}: always answering "
                f"'{_answer_text(p)}' is right {_pct(p['accuracy'], 1)} of the time",
            }
        b = blind.get(f)
        if b:
            points["blind"] = {
                "v": b["accuracy"],
                "lo": b.get("ci95_low"),
                "hi": b.get("ci95_high"),
                "tip": f"{f}: question only, {b['correct']}/{b['n']} = {_pct(b['accuracy'], 1)}",
            }
        m = model["families"].get(f) if model else None
        if m:
            points["model"] = {
                "v": m["accuracy"],
                "lo": m["ci95_low"],
                "hi": m["ci95_high"],
                "tip": f"{f}: {model['name']} with the image, {m['correct']}/{m['n']} = "
                f"{_pct(m['accuracy'], 1)}",
            }
        rows.append({"label": f, "points": points})
        table.append(
            {
                "family": f,
                "prior_answer": _answer_text(p) if p else None,
                "prior_before": before.get(f, {}).get("accuracy"),
                "prior": p["accuracy"] if p else None,
                "blind": b["accuracy"] if b else None,
                "blind_n": b["n"] if b else None,
                "blind_flag": bool(b and b.get("flag_blind_beats_prior")),
                "model": m["accuracy"] if m else None,
                "model_n": m["n"] if m else None,
            }
        )
    series = [("prior", "s3"), ("blind", "s2"), ("model", "s1")]
    return {
        "chart": dot_chart(rows, series, aria="Prior, blind and model accuracy by family"),
        "table": table,
        "blind_model": (d["blind"] or {}).get("model"),
        "model_name": model["name"] if model else None,
        "balance": d["balance"],
    }


def twin_section(d: dict[str, Any]) -> dict[str, Any]:
    gate = d["vr"].get("gates", {}).get("G6")
    tasks = {t.task_id: t for t in d["tasks"]}
    pairs = [(t.task_id, t.twin_id) for t in d["tasks"] if t.twin_id and t.twin_id in tasks]
    with_twin = sum(1 for t in d["tasks"] if t.twin_id)
    consistency = []
    for name, records in d["models"].items():
        correct = {r.task_id: r.correct for r in records}
        both = [(a, b) for a, b in pairs if a in correct and b in correct]
        if both:
            k = sum(correct[a] and correct[b] for a, b in both)
            consistency.append(
                {"model": name, "pairs": len(both), "both": k, "rate": k / len(both)}
            )
    return {"gate": gate, "pairs": len(pairs), "with_twin": with_twin, "consistency": consistency}


def render_section(d: dict[str, Any]) -> dict[str, Any] | None:
    scenes = (d["render"] or {}).get("scenes") or {}
    totals, phases = [], defaultdict(list)
    for s in scenes.values():
        timings = s.get("timings") or {}
        total = timings.get("total") or s.get("seconds")
        if total:
            totals.append(float(total))
        for p in PHASES:
            if timings.get(p) is not None:
                phases[p].append(float(timings[p]))
    if not totals:
        return None
    totals_sorted = sorted(totals)
    p90 = totals_sorted[min(len(totals_sorted) - 1, math.ceil(0.9 * len(totals_sorted)) - 1)]
    mean_phase = {p: statistics.fmean(v) for p, v in phases.items() if v}
    rows = [
        {
            "label": p.replace("_", " "),
            "value": v,
            "text": f"{v:.2f} s",
            "tip": f"{p}: mean {v:.2f} s per scene",
        }
        for p, v in mean_phase.items()
    ]
    status = Counter(s.get("status", "?") for s in scenes.values())
    return {
        "scenes": len(scenes),
        "timed": len(totals),
        "median": statistics.median(totals),
        "p90": p90,
        "mean": statistics.fmean(totals),
        "hours": sum(totals) / 3600,
        "device": d["render"].get("device"),
        "status": dict(status),
        "phase_chart": hbar_chart(rows, percent=False, series="s1", aria="Mean seconds per phase"),
        "hist": histogram(totals, bins=12, unit=" s", aria="Seconds per scene"),
    }


def dataset_report(run_dir: Path | str, out_dir: Path | str | None = None, echo=print) -> Path:
    from vtf.package.audit import template_env

    run_dir = Path(run_dir)
    d = collect(run_dir)
    lay: RunLayout = d["lay"]
    out = Path(out_dir) if out_dir is not None else lay.report_dir
    models = model_section(d)
    vr = d["vr"]
    tasks = d["tasks"]
    context = {
        "run_name": run_dir.resolve().name,
        "report_version": REPORT_VERSION,
        "run_info": d["run_info"],
        "versions": tasks[0].versions.model_dump() if tasks else {},
        "tiles": {
            "scenes": vr.get("scenes", len({t.scene_id for t in tasks})),
            "candidates": vr.get("candidates"),
            "accepted": len(tasks),
            "rejected": len(d["rejected"]),
            "families": len({t.family for t in tasks}),
            "rigs": len({t.rig for t in tasks}),
        },
        "gates": gate_section(d),
        "rejections": rejection_section(d),
        "coverage": coverage_section(d),
        "answers": answer_section(d),
        "conflict": conflict_section(d),
        "g5": g5_section(d, models),
        "twins": twin_section(d),
        "models": models,
        "audit": d["audit"],
        "render": render_section(d),
        "pct": _pct,
    }
    html_text = template_env().get_template("report.html.j2").render(**context)
    out.mkdir(parents=True, exist_ok=True)
    page = out / "index.html"
    page.write_text(html_text, encoding="utf-8")
    data = {k: v for k, v in context.items() if k in ("tiles", "versions", "run_name")}
    data["gates"] = context["gates"]["table"]
    data["models"] = [
        {
            k: m[k]
            for k in ("name", "n", "correct", "accuracy", "ci", "families", "rigs", "conflict")
        }
        for m in models
    ]
    (out / "report_data.json").write_text(json.dumps(data, indent=2, default=str) + "\n")
    echo(f"vtf dataset-report: {len(tasks)} tasks, {len(models)} model(s) -> {page}")
    return page
