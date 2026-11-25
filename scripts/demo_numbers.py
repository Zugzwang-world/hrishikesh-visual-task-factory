# ruff: noqa: E501  (slide and caption copy is prose)
"""Every number the demo pack quotes, from one place (brief VTF-09).

The slides (scripts/build_slides.py), the video captions, docs/submission.md, docs/QA.md and the
README headline all take their numbers from `numbers()`. Frozen v1 numbers come from
docs/reports/v1/findings.json. The three results we do not have yet stay as placeholders until
their Phase 2 outputs exist:

  {{AUDIT}}     the founder's blind audit vs the models  docs/reports/v1/human_vs_models.json
                                                         (scripts/audit_vs_models.py)
  {{SIM2REAL}}  real photos vs their renders             <twins>/sim_to_real/summary.json
                                                         (vtf twin sim-to-real; twins: out/twins_real)
  {{REAL_TRAP}} the hero photo (H1) result               <twins>/pairs.jsonl and the eval results
                                                         of <twins>/real and <twins>/sim

A missing output leaves its keys as None; callers show the {{KEY}} placeholder. docs/
submission_inputs.json ({"team_name", "team_leader", "video_url", "slides_url"}) holds what only
the founder can give: the registered team details and the two upload links. No model is called here; this only reads files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FINDINGS = ROOT / "docs/reports/v1/findings.json"
HUMAN = ROOT / "docs/reports/v1/human_vs_models.json"
INPUTS = ROOT / "docs/submission_inputs.json"
SELECTION = ROOT / "docs/twins/selection.json"

MODELS = [  # sweep key, short label (the Claude settings we tested)
    ("claude-haiku-5-5", "Haiku"),
    ("claude-sonnet-5-5", "Sonnet"),
    ("claude-opus-5-5", "Opus high"),
    ("claude-opus-5-5-xhigh", "Opus xhigh"),
]
PHASE2 = ["AUDIT", "AUDIT_SHORT", "AUDIT_SAY", "SIM2REAL", "SIM2REAL_SHORT", "SIM2REAL_ENDING", "REAL_TRAP",
          "REAL_TRAP_SHORT", "REAL_TRAP_ANSWER", "H1_PHOTO", "TEAM_NAME", "TEAM_LEADER", "VIDEO_URL",
          "SLIDES_URL"]  # fmt: skip
ENDING_REAL = "The failure is real."
ENDING_GAP = "The factory measures the sim-to-real gap."


def pct(x: float) -> str:
    return f"{round(100 * x)}%"


def pts(x: float) -> str:
    v = round(100 * x)
    return "0" if v == 0 else f"+{v}" if v > 0 else f"−{-v}"


def span(values: list[float], scale: float = 100, unit: str = "") -> str:
    """'10 to 13' from fractions; one value if they round the same."""
    lo, hi = round(min(values) * scale), round(max(values) * scale)
    return f"{lo}{unit}" if lo == hi else f"{lo} to {hi}{unit}"


def v1_numbers(findings: dict[str, Any]) -> dict[str, str]:
    m = findings["models"]
    n: dict[str, str] = {}
    agree = {k: m[k]["acc"]["by_conflict"]["no_conflict"]["accuracy"] for k, _ in MODELS}
    conflict = {k: m[k]["acc"]["by_conflict"]["conflict"]["accuracy"] for k, _ in MODELS}
    gaps = [m[k]["acc"]["gap"]["points"] for k, _ in MODELS]
    n["TASKS_PER_MODEL"] = f"{findings['tasks_common']:,}"
    n["SPATIAL_TASKS"] = f"{findings['spatial_common']:,}"
    n["CONFLICT_TASKS"] = str(m["claude-opus-5-5"]["acc"]["by_conflict"]["conflict"]["n"])
    n["GAP_RANGE"] = span(gaps)  # "24 to 26"
    n["GAP_ABOUT"] = str(round(sum(gaps) / len(gaps) * 100 / 5) * 5)  # "25"
    n["GAP_PAIRS"] = ", ".join(f"{lab} {pct(agree[k])} vs {pct(conflict[k])}" for k, lab in MODELS)
    for k, lab in MODELS:
        key = lab.upper().replace(" ", "_")
        n[f"AGREE_{key}"], n[f"CONFLICT_{key}"] = pct(agree[k]), pct(conflict[k])
    c = findings["comparisons"]["claude-opus-5-5-xhigh vs claude-opus-5-5"]
    lo, hi = c["gap_change_ci95"]
    n["XHIGH_GAP_CHANGE"] = f"{pts(c['gap_change_points'])} points (95% CI {pts(lo)} to {pts(hi)})"
    ng = m["claude-opus-5-5-xhigh"]["capture_by_family"]["nearest_to_gripper"]
    n["NEAREST_CAPTURE"] = (f"{ng['shortcut_picks']} of Opus xhigh's {ng['answered_misses']} misses "
                            f"name the object nearest in the image (chance {pct(ng['chance_share'])})")  # fmt: skip
    n["MATCHED_RANGE"] = span(
        [-m[k]["matched"]["gap_conflict_minus_no_conflict"] for k, _ in MODELS]
    )
    ors = [m[k]["logit"]["conflict_odds_ratio"] for k, _ in MODELS]
    n["CONFLICT_OR_RANGE"] = f"{min(ors):.2f} to {max(ors):.2f}"
    tf = findings["twin_flip"]
    both = [tf["models"][k]["both_right"]["share"] for k, _ in MODELS]
    sticks = [tf["models"][k]["sticks"]["share"] for k, _ in MODELS]
    n["TWIN_PAIRS"] = str(tf["pairs_total"])
    n["TWIN_BOTH_RIGHT"] = (f"{pct(both[0])} (Haiku) to {pct(max(both))} "
                            f"(Opus)")  # fmt: skip
    n["TWIN_STICKS"] = span(sticks, unit="%")
    n["TWIN_BOTH_SPAN"] = span(both, unit="%")  # "34 to 52%"
    n["XHIGH_GAP_POINTS"] = pts(c["gap_change_points"])  # "0"
    bp = findings["breaking_point"]["axes"]

    def last_minus_first(axis: str, keys: list[str], to: str | None = None) -> str:
        vals = []
        for k in keys:
            mm = bp[axis]["models"][k]
            v = mm["vs_first"][to]["adjusted"] if to else mm["last_minus_first"]["adjusted"]
            vals.append(-v)
        return span(vals)

    every = [k for k, _ in MODELS]
    n["HIDDEN_COST"] = last_minus_first("visible", every)  # "10 to 13"
    n["HARSH_COST_SONNET_OPUS"] = last_minus_first("camera", every[1:], to="harsh")  # "12 to 16"
    reg = findings["breaking_point"]["regression"]
    obj = [reg[k]["terms"]["objects_per_10"]["odds_ratio"] for k in every]
    n["OBJECTS_OR_RANGE"] = f"×{min(obj):.2f} to ×{max(obj):.2f}"
    n["TWIN_COST_USD"] = f"{tf['cost']['total_usd']:.2f}"
    return n


def audit_numbers(path: Path = HUMAN) -> dict[str, str]:
    if not path.is_file():
        return {}
    h = json.loads(path.read_text())
    allsplit = h["splits"]["all"]
    hu = allsplit["human"]
    if not hu.get("n") or hu.get("accuracy") is None:
        return {}
    models = allsplit["models"]
    accs = {k: models[k]["accuracy"] for k, _ in MODELS if k in models and models[k]["accuracy"]}
    lo, hi = hu["ci95"] if hu.get("ci95") else (None, None)
    ci = f" (95% CI {pct(lo)} to {pct(hi)})" if lo is not None else ""
    model_span = span(list(accs.values()), unit="%") if accs else "n/a"
    conf = h["splits"].get("conflict", {}).get("human", {})
    conf_txt = ""
    if conf.get("n") and conf.get("accuracy") is not None:
        conf_txt = (f"; on the {conf['n']} tasks where the 2D shortcut misleads, "
                    f"{conf['correct']} of {conf['n']} ({pct(conf['accuracy'])})")  # fmt: skip
    return {
        "AUDIT": (
            f"The founder, answering blind, got {hu['correct']} of {hu['n']} audited tasks "
            f"right, {pct(hu['accuracy'])}{ci}{conf_txt}. The Claude models we tested scored "
            f"{model_span} on the same tasks."
        ),  # fmt: skip
        "AUDIT_SHORT": f"Human {pct(hu['accuracy'])} vs Claude models {model_span}",
        # the video's spoken line: claim "a person gets them right" only when the audit shows it
        "AUDIT_SAY": (("A person gets them right. " if hu["accuracy"] >= 0.9 else "")
                      + f"In my blind audit: human {pct(hu['accuracy'])}, the Claude models we tested {model_span}."),
    }


def _results(folder: Path) -> dict[str, dict[str, dict]]:
    """{model key: {task_id: result row}} from <run>/eval/<model>/results.jsonl."""
    out: dict[str, dict[str, dict]] = {}
    for f in sorted(folder.glob("eval/*/results.jsonl")):
        rows = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
        out[f.parent.name] = {r["task_id"]: r for r in rows}
    return out


def sim2real_numbers(twins: Path) -> dict[str, str]:
    path = twins / "sim_to_real" / "summary.json"
    if not path.is_file():
        return {}
    rep = json.loads(path.read_text())["models"]
    rows = [(lab, rep[k]) for k, lab in MODELS if k in rep and rep[k].get("pairs")]
    if not rows:
        return {}
    parts, same = [], True
    for lab, s in rows:
        ci = s.get("ci95", {}).get("difference")
        parts.append(
            f"{lab} {pct(s['accuracy_real'])} on photos vs {pct(s['accuracy_sim'])} on renders"
        )
        if ci and not (ci[0] <= 0 <= ci[1]):
            same = False
    pairs, layouts = rows[0][1]["pairs"], rows[0][1]["layouts"]
    ending = ENDING_REAL if same else ENDING_GAP
    reals = [s["accuracy_real"] for _, s in rows]
    sims = [s["accuracy_sim"] for _, s in rows]
    return {
        "SIM2REAL": (
            f"On {pairs} real-photo tasks from {layouts} layouts: "
            + "; ".join(parts)
            + (
                ". No setting's photo-minus-render difference is distinguishable from zero."
                if same
                else ". For at least one setting, zero is outside the difference's 95% interval."
            )
        ),  # fmt: skip
        "SIM2REAL_SHORT": f"photos {span(reals, unit='%')} vs renders {span(sims, unit='%')}",
        "SIM2REAL_ENDING": ending,
    }


def real_trap_numbers(twins: Path) -> dict[str, str]:
    pairs_path = twins / "pairs.jsonl"
    if not pairs_path.is_file():
        return {}
    pairs = [json.loads(x) for x in pairs_path.read_text().splitlines() if x.strip()]
    sel = json.loads(SELECTION.read_text()) if SELECTION.is_file() else {}
    film = None  # H1's first pinned question is the one to film (docs/twins/trap_check.md)
    for lay in sel.get("hero", {}).get("layouts", []):
        if lay.get("label") == "H1" and lay.get("pinned"):
            film = lay["pinned"][0]
    real_res, sim_res = _results(twins / "real"), _results(twins / "sim")
    live = ROOT / "out/video/live_call.json"  # scripts/ask_live.py: the call the video films
    live = json.loads(live.read_text()) if live.is_file() else {}

    def say(v: Any) -> str:
        return {True: "yes", False: "no"}.get(v, str(v)) if isinstance(v, bool) or v is None else str(v)

    def answered(r: dict | None) -> bool:
        return bool(r) and not r.get("error") and r.get("parse_ok", r.get("parsed") is not None)

    # the front photo first (on the spot), then the other cameras; register saves H1_<camera>.png
    order = sorted(pairs, key=lambda p: (p.get("camera_id") != "front", p.get("camera_id", ""), p.get("photo", "")))
    for p in order:
        if p.get("layout") != "H1":
            continue
        task_json = twins / "real" / "tasks" / p["real_task_id"] / "task.json"
        if not task_json.is_file():
            continue
        task = json.loads(task_json.read_text())
        prog = task.get("program", {})
        if film and (prog.get("name") != film.get("program") or prog.get("params") != film.get("params")):
            continue
        shortcut = say(task.get("shortcut_answer"))
        said, took, asked, render_took, render_asked, film_answer = [], 0, 0, 0, 0, None
        for k, lab in MODELS:
            r = real_res.get(k, {}).get(p["real_task_id"])
            if not answered(r):
                continue
            ans = say(r.get("parsed"))
            asked += 1
            took += ans == shortcut
            said.append(f"{lab} says {ans}")
            if k == "claude-opus-5-5":  # the setting the video asks live
                film_answer = ans
            rs = sim_res.get(k, {}).get(p["sim_task_id"])
            if answered(rs):
                render_asked += 1
                render_took += say(rs.get("parsed")) == shortcut
        if not asked:
            continue
        if live.get("task_id") == p["real_task_id"] and live.get("answer") is not None:
            film_answer = say(live["answer"])  # what the screen shows in shot S1
        truth = say(task.get("answer"))
        verdict = f"{took} of {asked} Claude settings we tested take the 2D shortcut ({shortcut})"
        if render_asked:
            verdict += f"; on its render, {render_took} of {render_asked}"
        photo = twins / "images" / f"H1_{p.get('camera_id', 'front')}.png"
        if not photo.is_file():
            photo = next(iter(sorted((twins / "images").glob(f"H1_{p.get('camera_id', 'front')}*"))), photo)
        return {
            "REAL_TRAP": f"On the real H1 photo (truth: {truth}), {verdict}. " + ", ".join(said) + ".",
            "REAL_TRAP_SHORT": verdict,
            "REAL_TRAP_ANSWER": film_answer,
            "H1_PHOTO": str(photo) if photo.is_file() else None,
        }
    return {}


def numbers(twins: Path | None = None) -> dict[str, str | None]:
    if twins is None:
        twins = ROOT / "out/twins_real"
        if not twins.exists() and ROOT.parent.name == "worktrees":  # a worktree: use the main checkout's
            twins = ROOT.parents[2] / "out/twins_real"
    twins = Path(twins)
    n: dict[str, str | None] = {k: None for k in PHASE2}
    n.update(v1_numbers(json.loads(FINDINGS.read_text())))
    n.update(audit_numbers())
    n.update(sim2real_numbers(twins))
    n.update(real_trap_numbers(twins))
    if INPUTS.is_file():
        t = json.loads(INPUTS.read_text())
        for key in ("team_name", "team_leader", "video_url", "slides_url"):
            n[key.upper()] = t.get(key) or None
    return n


if __name__ == "__main__":
    for k, v in numbers().items():
        print(f"{k:24} {v if v is not None else '{{' + k + '}}  (placeholder)'}")
