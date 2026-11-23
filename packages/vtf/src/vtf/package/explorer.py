"""Task Explorer export: a small, self-contained sample of a run for the web viewer (web/).

    vtf export <run> --format explorer --out web/public/data --scenes 6 \\
        [--results <dir> ...] [--gallery <findings.json>]

writes

    <out>/index.json                 run summary, models, the exported scenes, the conflict gallery
    <out>/<scene_id>/scene.json      cameras (K, world_to_camera, rig, image), settled objects,
                                     table, gripper, zones, tasks with gates and model answers
    <out>/<scene_id>/<camera>.jpg    beauty image per camera, at most `max_width` px wide
    <out>/<scene_id>/thumb.jpg       small picker thumbnail
    <out>/<scene_id>/thumb_<camera>.jpg  gallery card thumbnail (gallery scenes only)
    <out>/<scene_id>/scene.glb       display glTF from render/scene.glb, compressed with meshopt
                                     geometry and WebP textures (web/scripts/compress-glb.mjs),
                                     or the plain copy when compression is off or fails

Model answers come from result folders (`results`: each holds results.jsonl and summary.json,
labelled by the folder name, e.g. out/v1/sweep/claude-opus-5-5-xhigh), or by default from
<run>/eval/*/. The gallery (`gallery`: a JSON file with a "gallery" list, as in
docs/reports/v1/findings.json) adds its scenes to the coverage pick and an index.json "gallery"
list. For shortcut-conflict tasks the export names the true-answer and 2D-shortcut objects by id
(`truth_object_ids`, `shortcut_object_ids`), so the viewer can mark them in every view.

Everything is read from the run folder as plain JSON and tolerant of older contracts: a field a
v0.1 run does not have (rig, shortcut_conflict, question_alt, glTF, ...) is exported as null. The
export is a pure function of the run folder and the options (no timestamps), so re-exporting the
same run gives the same files.
"""

import json
import math
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from vtf.camera.dial import distort_points
from vtf.core import layout
from vtf.core.io import read_jsonl
from vtf.core.program import FAMILIES
from vtf.core.vocab import resolve_object

EXPLORER_VERSION = "0.2.0"
GATE_IDS = ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8")
JPEG_QUALITY = 80
THUMB_WIDTH = 360
GALLERY_THUMB_WIDTH = 480
GLB_SCRIPT = Path(__file__).resolve().parents[5] / "web" / "scripts" / "compress-glb.mjs"
_TIERS = {"haiku": 0, "sonnet": 1, "opus": 2}
_EFFORTS = {"low": 0, "medium": 1, "high": 2, "xhigh": 3, "max": 4}
GALLERY_MODEL_KEYS = (
    "label", "display_name", "effort", "with_image", "answer", "parse_ok", "correct",
    "took_shortcut",
)  # fmt: skip
MAX_LIST = 16
MAX_KEY_ITEMS = 8
MAX_RAW = 400
KEY_SKIP = {"program", "answer"}


# ---------------------------------------------------------------- small helpers


def _read_json(path: Path) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=1, ensure_ascii=False, allow_nan=False)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def _is_hash(value: str) -> bool:
    return len(value) >= 32 and all(c in "0123456789abcdef" for c in value)


def _round(value: float, places: int = 6) -> float | None:
    if not math.isfinite(value):
        return None
    return round(value, places) + 0.0


def _compact(value: Any, depth: int = 0) -> Any:
    """Evidence for display: floats rounded, hashes shortened, long lists and deep trees cut."""
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return value
    if isinstance(value, float):
        return _round(value)
    if isinstance(value, str):
        return value[:12] + "…" if _is_hash(value) else value[:MAX_RAW]
    if depth >= 4:
        return "…"
    if isinstance(value, dict):
        return {str(k): _compact(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple):
        out = [_compact(v, depth + 1) for v in list(value)[:MAX_LIST]]
        if len(value) > MAX_LIST:
            out.append(f"… {len(value) - MAX_LIST} more")
        return out
    return str(value)


def _scalar_text(value: Any) -> str | None:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.4g}" if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:12] + "…" if _is_hash(value) else value[:80]
    return None


def key_evidence(evidence: dict) -> list[list[str]]:
    """The headline numbers of one gate's evidence, as [label, text] pairs for a one-line view.

    Generic over gates: top-level scalars, small flat dicts (params), the decision steps of a
    program trace, and per-object scalars (G3 visibility) summarised by their weakest object.
    """
    if not isinstance(evidence, dict):
        return []
    # Buckets in display order: decisions, per-object summaries, booleans, numbers, flat dicts,
    # other strings (hashes last). Keys the task card already shows are skipped.
    decision, objects, bools, numbers, flats, strings = [], [], [], [], [], []
    for key, value in evidence.items():
        text = _scalar_text(value)
        if key in KEY_SKIP:
            continue
        if text is not None:
            if isinstance(value, bool):
                bools.append([key, text])
            elif isinstance(value, int | float):
                numbers.append([key, text])
            else:
                strings.append([key, text])
        elif (
            isinstance(value, dict)
            and value
            and all(_scalar_text(v) is not None for v in value.values())
        ):
            if len(value) <= 4:
                flats.append([key, ", ".join(f"{k}={_scalar_text(v)}" for k, v in value.items())])
        elif key == "trace" and isinstance(value, list):
            for step in value:
                if isinstance(step, dict) and step.get("step") == "decision":
                    for k, v in step.items():
                        text = _scalar_text(v)
                        if k != "step" and text is not None:
                            decision.append([k, text])
        elif isinstance(value, dict) and value and all(isinstance(v, dict) for v in value.values()):
            # e.g. G3 objects: {obj_01: {visible_fraction, visible_pixels, pass}, ...}
            rows = list(value.items())
            frac_key = next(
                (k for k in ("visible_fraction", "fraction", "share", "margin") if k in rows[0][1]),
                None,
            )
            if frac_key is not None:
                weakest_id, weakest = min(
                    rows, key=lambda kv: kv[1].get(frac_key, math.inf)
                    if isinstance(kv[1].get(frac_key), int | float) else math.inf
                )  # fmt: skip
                parts = [f"{_human(frac_key)} {_scalar_text(weakest.get(frac_key))}"]
                for extra in ("visible_pixels", "pixels"):
                    if extra in weakest:
                        parts.append(f"{_scalar_text(weakest[extra])} px")
                objects.append([f"weakest of {len(rows)}: {weakest_id}", ", ".join(parts)])
    items = decision + objects + bools + numbers + flats + strings
    return [[_human(k), v] for k, v in items[:MAX_KEY_ITEMS]]


def _human(key: str) -> str:
    """Evidence keys read as words (gap_m -> gap m); object ids (obj_03) stay as they are."""
    return " ".join(w if w.startswith("obj_") else w.replace("_", " ") for w in key.split(" "))


def _gates(task: dict) -> dict:
    out = {}
    for gate_id, result in (task.get("gates") or {}).items():
        if not isinstance(result, dict):
            continue
        evidence = result.get("evidence") or {}
        out[gate_id] = {
            "pass": bool(result.get("pass", result.get("passed", False))),
            "key": key_evidence(evidence),
            "evidence": _compact(evidence),
        }
    return out


# ---------------------------------------------------------------- run reading


def _load_tasks(run: Path) -> dict[str, list[dict]]:
    """scene_id -> accepted tasks (raw task.json dicts), in manifest order."""
    lay = layout.RunLayout(run)
    by_scene: dict[str, list[dict]] = defaultdict(list)
    if not lay.task_manifest.exists():
        return by_scene
    for row in read_jsonl(lay.task_manifest):
        path = run / row.get("path", f"{layout.TASKS_DIR}/{row['task_id']}/{layout.TASK_FILE}")
        task = _read_json(path)
        if task is None:
            continue
        for key in ("rig", "shortcut_conflict"):
            if task.get(key) is None and row.get(key) is not None:
                task[key] = row[key]
        by_scene[task["scene_id"]].append(task)
    return by_scene


def model_display_name(model_id: str | None, effort: str | None, with_image: bool = True) -> str:
    """'claude-opus-5-5', 'xhigh' -> 'Opus 5.5 · xhigh'; a question-only run adds '· blind'."""
    base = (model_id or "model").split("__")[0]
    m = re.fullmatch(r"claude-([a-z]+)-(\d{1,2}(?:-\d{1,2})?)(?:-\d{8})?", base)
    name = f"{m[1].capitalize()} {m[2].replace('-', '.')}" if m else base
    return " · ".join([name, *([effort] if effort else []), *([] if with_image else ["blind"])])


def _model_summary(label: str, recs: dict[str, dict], summary_file: dict, source: str) -> dict:
    first = next(iter(recs.values()))
    settings = summary_file.get("settings") or {}
    with_image = summary_file.get("with_image")
    if with_image is None:
        with_image = first.get("with_image", True) is not False
    model_id = summary_file.get("model_id") or first.get("model_id") or first.get("model")
    effort = settings.get("effort") or first.get("effort")
    n = len(recs)
    correct = sum(1 for r in recs.values() if r.get("correct"))
    return {
        "label": label,
        "model": model_id,
        "display_name": model_display_name(model_id, effort, bool(with_image)),
        "effort": effort,
        "with_image": bool(with_image),
        "source": source,
        "n": n,
        "correct": correct,
        "accuracy": _round(correct / n, 4) if n else None,
        "prompt_version": summary_file.get("prompt_version"),
        "settings": settings or None,
    }


def _model_order(summary: dict) -> tuple:
    """Image runs first, then Haiku, Sonnet, Opus, then effort; question-only (blind) runs last."""
    model = (summary.get("model") or "").lower()
    tier = next((rank for name, rank in _TIERS.items() if name in model), len(_TIERS))
    effort = _EFFORTS.get(summary.get("effort") or "", len(_EFFORTS))
    return (not summary["with_image"], tier, effort, summary["label"])


def _ordered(
    summaries: list[dict], results: dict[str, dict[str, dict]]
) -> tuple[list[dict], dict[str, dict[str, dict]]]:
    summaries = sorted(summaries, key=_model_order)
    return summaries, {s["label"]: results[s["label"]] for s in summaries}


def _load_models(run: Path) -> tuple[list[dict], dict[str, dict[str, dict]]]:
    """Model summaries and label -> task_id -> EvalRecord dict, from eval/*/results.jsonl."""
    eval_root = run / layout.EVAL_DIR
    summaries: list[dict] = []
    results: dict[str, dict[str, dict]] = {}
    if not eval_root.exists():
        return summaries, results
    for results_path in sorted(eval_root.glob(f"*/{layout.EVAL_RESULTS}")):
        records = list(read_jsonl(results_path))
        if not records:
            continue
        by_label: dict[str, dict[str, dict]] = defaultdict(dict)
        for rec in records:
            label = rec.get("model") or results_path.parent.name
            if rec.get("with_image") is False:
                label += " (blind)"
            by_label[label][rec["task_id"]] = rec
        summary_file = _read_json(results_path.parent / layout.EVAL_SUMMARY) or {}
        for label, recs in sorted(by_label.items()):
            if label in results:
                results[label].update(recs)
                continue
            results[label] = recs
            blind = next(iter(recs.values())).get("with_image") is False
            file = {**summary_file, "with_image": not blind}
            summaries.append(_model_summary(label, recs, file, "eval"))
    return _ordered(summaries, results)


def load_results_dirs(dirs: Sequence[Path]) -> tuple[list[dict], dict[str, dict[str, dict]]]:
    """Model summaries and label -> task_id -> EvalRecord dict from result folders, each with
    results.jsonl and summary.json, labelled by the folder name (the sweep key)."""
    summaries: list[dict] = []
    results: dict[str, dict[str, dict]] = {}
    for folder in dirs:
        folder = Path(folder)
        results_path = folder / layout.EVAL_RESULTS
        if not results_path.exists():
            print(f"explorer: no {layout.EVAL_RESULTS} in {folder}; skipped")
            continue
        recs = {rec["task_id"]: rec for rec in read_jsonl(results_path)}
        if not recs:
            print(f"explorer: {results_path} is empty; skipped")
            continue
        label = folder.name
        if label in results:
            label = f"{folder.parent.name}/{folder.name}"
        results[label] = recs
        summary_file = _read_json(folder / layout.EVAL_SUMMARY) or {}
        summaries.append(_model_summary(label, recs, summary_file, "results"))
    return _ordered(summaries, results)


# ---------------------------------------------------------------- object ids for answers


@dataclass(frozen=True)
class _Named:
    id: str
    category: str
    color_name: str | None
    size_word: str | None


def _nameables(objects: list[dict]) -> list[_Named]:
    return [
        _Named(o["id"], o.get("category") or "", o.get("color_name"), o.get("size_word"))
        for o in objects
        if o.get("id")
    ]


def named_ids(value: Any, objects: Sequence[_Named]) -> list[str] | None:
    """Scene object ids an object answer (a name) or a set answer (a list of names) names, by the
    naming rule (vtf.core.vocab.resolve_object, as the scorer resolves answers). None when a name
    does not pick out exactly one object; [] for no name, 'none' or 'table'."""
    if value is None:
        return []
    names = value if isinstance(value, list) else [value]
    ids: set[str] = set()
    for name in names:
        r = resolve_object(name, objects)
        if r.kind == "object":
            ids.update(r.ids)
        elif r.kind not in ("table", "none"):
            return None
    return sorted(ids)


def _trace_shortcut_ids(task: dict, known: set[str]) -> list[str]:
    """The objects the 2D heuristic picked, as the program's own G1 trace records them."""
    g1 = (task.get("gates") or {}).get("G1") or {}
    trace = (g1.get("evidence") or {}).get("trace") or []
    for step in trace:
        if isinstance(step, dict) and step.get("step") == "shortcut":
            ids = step.get("object_ids") or ([step["object_id"]] if step.get("object_id") else [])
            return sorted(i for i in ids if i in known)
    return []


def highlight_ids(task: dict, objects: Sequence[_Named]) -> tuple[list[str], list[str]]:
    """(truth_object_ids, shortcut_object_ids) for a task.

    Object and set answers: the objects the true answer and the 2D-shortcut answer name, by the
    naming rule against the scene's full object list. A shortcut name that names no single object
    (three vases, one of them unnameable) falls back to the object ids in the program's G1 trace
    shortcut step; without one it is []. Bool answers (free space): the evidence objects carry
    the true answer and no object stands for the shortcut.
    """
    if task.get("answer_type") == "bool":
        return list(task.get("evidence_object_ids") or []), []
    truth = named_ids(task.get("answer"), objects) or []
    shortcut = named_ids(task.get("shortcut_answer"), objects)
    if shortcut is None:
        shortcut = _trace_shortcut_ids(task, {o.id for o in objects})
    return truth, shortcut


def _took_shortcut(
    task: dict,
    parsed: Any,
    objects: Sequence[_Named],
    truth_ids: list[str],
    shortcut_ids: list[str],
) -> bool:
    """Is a wrong answer the 2D-shortcut answer? Bool: the same value. Set: the same objects.
    Object: the objects the name could mean include the shortcut object and not the true one (so
    'brown vase' takes the shortcut when it is one of three vases and the truth is an onion)."""
    if task.get("answer_type") == "bool":
        return parsed == task.get("shortcut_answer")
    if task.get("answer_type") == "set":
        return named_ids(parsed, objects) == shortcut_ids
    if isinstance(parsed, str) and parsed == task.get("shortcut_answer"):
        return True
    r = resolve_object(parsed, objects)
    could_mean = set(r.ids) if r.kind in ("object", "ambiguous") else set()
    return (
        bool(shortcut_ids) and set(shortcut_ids) <= could_mean and not could_mean & set(truth_ids)
    )


def _model_answer(
    label: str,
    rec: dict,
    model: dict | None,
    task: dict,
    objects: Sequence[_Named],
    highlights: tuple[list[str], list[str]],
) -> dict:
    parsed = rec.get("parsed") if rec.get("parse_ok") else None
    if rec.get("parse_ok"):
        shown = parsed
    else:
        shown = (rec.get("raw_response") or rec.get("error") or "")[:MAX_RAW]
    answer_ids: list[str] = []
    if rec.get("parse_ok") and task.get("answer_type") in ("object", "set"):
        answer_ids = named_ids(parsed, objects) or []
    took = None
    if task.get("shortcut_conflict"):
        wrong = rec.get("parse_ok") and not rec.get("correct")
        took = bool(wrong) and _took_shortcut(task, parsed, objects, *highlights)
    model = model or {}
    return {
        "label": label,
        "model": rec.get("model_id") or rec.get("model"),
        "display_name": model.get("display_name") or label,
        "effort": model.get("effort"),
        "with_image": rec.get("with_image", True),
        "answer": _compact(shown),
        "answer_object_ids": answer_ids,
        "parse_ok": bool(rec.get("parse_ok")),
        "correct": bool(rec.get("correct")),
        "took_shortcut": took,
        "raw_response": (rec.get("raw_response") or "")[:MAX_RAW] or None,
        "error": rec.get("error"),
    }


# ---------------------------------------------------------------- scene selection


def select_scenes(
    tasks_by_scene: dict[str, list[dict]],
    results: dict[str, dict[str, dict]],
    n: int,
    eligible: set[str] | None = None,
) -> list[str]:
    """Greedy pick: each round, the scene adding the most families not yet covered; ties go to
    scenes with more families, more wrong model answers (they make the demo), more tasks, then
    the lower scene id. Deterministic."""
    candidates = sorted(s for s in tasks_by_scene if eligible is None or s in eligible)
    rank = {s: i for i, s in enumerate(candidates)}
    wrong_by_task = Counter(
        tid for recs in results.values() for tid, r in recs.items() if not r.get("correct")
    )
    fams = {s: {t["family"] for t in tasks_by_scene[s]} for s in candidates}
    stats = {
        s: (
            len(fams[s]),
            sum(wrong_by_task[t["task_id"]] for t in tasks_by_scene[s]),
            len(tasks_by_scene[s]),
        )
        for s in candidates
    }
    picked: list[str] = []
    covered: set[str] = set()
    while candidates and len(picked) < n:
        best = max(candidates, key=lambda s: (len(fams[s] - covered), *stats[s], -rank[s]))
        picked.append(best)
        covered |= fams[best]
        candidates.remove(best)
    return picked


# ---------------------------------------------------------------- per-scene export


def _save_jpeg(src: Path, dst: Path, max_width: int) -> tuple[int, int]:
    with Image.open(src) as img:
        img = img.convert("RGB")
        if img.width > max_width:
            h = round(img.height * max_width / img.width)
            img = img.resize((max_width, h), Image.LANCZOS)
        dst.parent.mkdir(parents=True, exist_ok=True)
        img.save(dst, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
        return img.width, img.height


def _object_entry(obj: dict) -> dict:
    words = (obj.get("size_word"), obj.get("color_name"), obj.get("category"))
    pose = obj.get("pose") or {}
    return {
        "id": obj["id"],
        "name": " ".join(w for w in words if w),
        "category": obj.get("category"),
        "shape": obj.get("shape"),
        "source": obj.get("source", "primitive"),
        "color_name": obj.get("color_name"),
        "size_word": obj.get("size_word"),
        "rgb": obj.get("rgb"),
        "position": pose.get("position"),
        "rotation": pose.get("rotation"),
        "dimensions": obj.get("dimensions"),
        "pass_index": obj.get("pass_index"),
        "placement": obj.get("placement"),
    }


def _task_entry(
    task: dict,
    cam_rigs: dict[str, str | None],
    results: dict,
    models: dict[str, dict],
    objects: Sequence[_Named],
) -> dict:
    truth_ids, shortcut_ids = highlight_ids(task, objects)
    highlights = (truth_ids, shortcut_ids)
    answers = [
        _model_answer(label, recs[task["task_id"]], models.get(label), task, objects, highlights)
        for label, recs in results.items()
        if task["task_id"] in recs
    ]
    entry = {
        "task_id": task["task_id"],
        "task_version": task.get("task_version"),
        "camera_id": task.get("camera_id"),
        "rig": task.get("rig") or cam_rigs.get(task.get("camera_id")),
        "family": task.get("family"),
        "template_id": task.get("template_id"),
        "program": task.get("program"),
        "question": task.get("question"),
        "question_alt": task.get("question_alt"),
        "answer": _compact(task.get("answer")),
        "answer_type": task.get("answer_type"),
        "answer_text": task.get("answer_text"),
        "tolerance": task.get("tolerance"),
        "shortcut_conflict": task.get("shortcut_conflict"),
        "shortcut_answer": _compact(task.get("shortcut_answer")),
        "evidence_object_ids": task.get("evidence_object_ids") or [],
        "difficulty": _compact(task.get("difficulty") or {}),
        "gates": _gates(task),
        "twin_of": task.get("twin_of"),
        "twin_id": task.get("twin_id"),
        "models": answers,
    }
    if task.get("shortcut_conflict"):
        entry["truth_object_ids"] = truth_ids
        entry["shortcut_object_ids"] = shortcut_ids
    return entry


def compress_glb(src: Path, dst: Path, script: Path | None = None) -> str | None:
    """Write a meshopt + WebP copy of `src` to `dst` with web/scripts/compress-glb.mjs (Node and
    web/node_modules). Returns None on success, else why it failed (dst is then not written)."""
    script = Path(script or GLB_SCRIPT)
    node = shutil.which("node")
    if node is None:
        return "node not found"
    if not script.exists():
        return f"{script} not found"
    try:
        proc = subprocess.run(
            [node, str(script), str(src), str(dst)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as err:
        return f"node failed: {err}"
    rows = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
    if proc.returncode != 0 or not rows:
        return (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()[:300]
    if not rows[-1].get("ok") or not Path(dst).exists():
        return str(rows[-1].get("error") or "no output")
    return None


def _scene_layout(run: Path, scene_id: str) -> layout.RunLayout:
    """scenes/<id> or, for a counterfactual twin, twins/<id>."""
    lay = layout.RunLayout(run)
    if lay.render_manifest(scene_id).exists() or lay.scene_file(scene_id).exists():
        return lay
    twins = lay.twin_layout()
    return twins if twins.scene_dir(scene_id).exists() else lay


def project_uv(cam: dict, point: Sequence[float]) -> list[float] | None:
    """Where a Blender-world point lands in a camera's image, as fractions of width and height
    (with the camera's lens distortion when its quality dials one in), or None off-frame."""
    w2c, k, res = cam.get("world_to_camera"), cam.get("K"), cam.get("resolution")
    if not (w2c and k and res and point):
        return None
    m = np.asarray(w2c, dtype=np.float64)
    pc = m[:3, :3] @ np.asarray(point, dtype=np.float64) + m[:3, 3]
    if pc[2] <= 1e-6:
        return None
    kk = np.asarray(k, dtype=np.float64)
    uv = np.array([[kk[0, 0] * pc[0] / pc[2] + kk[0, 2], kk[1, 1] * pc[1] / pc[2] + kk[1, 2]]])
    k1 = float((cam.get("quality") or {}).get("distortion_k1") or 0.0)
    if k1:
        uv = distort_points(uv, k1, kk)
    u, v = float(uv[0, 0]) / res[0], float(uv[0, 1]) / res[1]
    if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
        return None
    return [round(u, 4) + 0.0, round(v, 4) + 0.0]


def export_scene(
    run: Path,
    scene_id: str,
    tasks: list[dict],
    results: dict,
    out: Path,
    max_width: int,
    models: dict[str, dict] | None = None,
    compress: bool = False,
    glb_script: Path | None = None,
    thumb_cameras: Sequence[str] = (),
) -> dict:
    """Write <out>/<scene_id>/ and return its index.json summary."""
    lay = _scene_layout(run, scene_id)
    render_dir = lay.render_dir(scene_id)
    manifest = _read_json(render_dir / layout.RENDER_MANIFEST) or {}
    settled = _read_json(render_dir / manifest.get("settled_scene_file", layout.SETTLED_SCENE))
    scene = settled or _read_json(lay.scene_file(scene_id))
    if scene is None:
        raise FileNotFoundError(f"{scene_id}: no scene file")
    scene_out = out / scene_id
    scene_out.mkdir(parents=True, exist_ok=True)

    spec_cams = {c["id"]: c for c in scene.get("cameras", [])}
    cameras = []
    for cam in manifest.get("cameras", []):
        cid = cam["camera_id"]
        spec = spec_cams.get(cid, {})
        image = None
        image_size = None
        beauty = render_dir / cam["beauty"] if cam.get("beauty") else None
        if beauty is not None and beauty.exists():
            image = f"{cid}.jpg"
            image_size = list(_save_jpeg(beauty, scene_out / image, max_width))
        cameras.append(
            {
                "camera_id": cid,
                "rig": cam.get("rig") or spec.get("rig"),
                "image": image,
                "image_size": image_size,
                "resolution": cam.get("resolution") or spec.get("resolution"),
                "K": cam.get("K"),
                "world_to_camera": cam.get("world_to_camera"),
                "focal_mm": spec.get("focal_mm"),
                "sensor_width_mm": spec.get("sensor_width_mm"),
                "quality": cam.get("quality") or spec.get("quality"),
            }
        )
    order = {"overhead": 0, "front": 1, "gripper": 2}
    cameras.sort(key=lambda c: (order.get(c["rig"] or "", 9), c["camera_id"]))

    thumb = None
    first = next((c for c in cameras if c["image"]), None)
    if first is not None:
        thumb = "thumb.jpg"
        _save_jpeg(scene_out / first["image"], scene_out / thumb, THUMB_WIDTH)
    thumbs = {}
    for cid in sorted(set(thumb_cameras)):
        cam = next((c for c in cameras if c["camera_id"] == cid and c["image"]), None)
        if cam is not None:
            thumbs[cid] = f"thumb_{cid}.jpg"
            _save_jpeg(scene_out / cam["image"], scene_out / thumbs[cid], GALLERY_THUMB_WIDTH)

    gltf = None
    compressed = False
    glb_name = manifest.get("gltf") or layout.GLTF
    if (render_dir / glb_name).exists():
        gltf = layout.GLTF
        why = compress_glb(render_dir / glb_name, scene_out / gltf, glb_script) if compress else ""
        compressed = why is None
        if not compressed:
            if why:
                print(f"explorer: {scene_id}: glb compression failed ({why}); copied the plain glb")
            shutil.copyfile(render_dir / glb_name, scene_out / gltf)

    meta = scene.get("meta", {})
    gripper = scene.get("gripper") or {}
    cam_rigs = {c["camera_id"]: c["rig"] for c in cameras}
    objects = _nameables(scene.get("objects", []))
    task_entries = [_task_entry(t, cam_rigs, results, models or {}, objects) for t in tasks]
    data = {
        "explorer_version": EXPLORER_VERSION,
        "scene_id": scene_id,
        "spec_version": meta.get("spec_version"),
        "seed": meta.get("seed"),
        "settled": meta.get("settled"),
        "twin_of": meta.get("twin_of"),
        "difficulty": meta.get("difficulty"),
        "gltf": gltf,
        "gltf_up": "y" if gltf else None,
        "gltf_compressed": compressed if gltf else None,
        "table": scene.get("table"),
        "gripper": {
            "tip": gripper.get("tip"),
            "opening": gripper.get("opening"),
            "parts": gripper.get("parts", []),
        },
        "zones": scene.get("zones", []),
        "lighting": {
            k: (scene.get("lighting") or {}).get(k) for k in ("hdri_id", "condition", "key_light")
        },
        "renderer": {
            "blender_version": manifest.get("blender_version"),
            "device": manifest.get("device"),
            "samples": manifest.get("samples"),
            "manifest_version": manifest.get("manifest_version"),
        },
        "cameras": cameras,
        "objects": [_object_entry(o) for o in scene.get("objects", [])],
        "tasks": task_entries,
    }
    _write_json(scene_out / "scene.json", data)

    model_scores: dict[str, dict[str, int]] = {}
    for t in task_entries:
        for m in t["models"]:
            s = model_scores.setdefault(m["label"], {"n": 0, "correct": 0})
            s["n"] += 1
            s["correct"] += int(m["correct"])
    return {
        "scene_id": scene_id,
        "path": f"{scene_id}/scene.json",
        "thumb": f"{scene_id}/{thumb}" if thumb else None,
        "thumbs": {cid: f"{scene_id}/{name}" for cid, name in thumbs.items()},
        "n_tasks": len(task_entries),
        "n_objects": len(data["objects"]),
        "families": dict(sorted(Counter(t["family"] for t in task_entries).items())),
        "cameras": [{"camera_id": c["camera_id"], "rig": c["rig"]} for c in cameras],
        "gltf": gltf is not None,
        "gltf_compressed": compressed,
        "twin_of": meta.get("twin_of"),
        "shortcut_conflicts": sum(1 for t in task_entries if t["shortcut_conflict"]),
        "models": model_scores,
    }


# ---------------------------------------------------------------- conflict gallery


def load_gallery(path: Path) -> list[dict]:
    """The "gallery" list of a findings file (docs/reports/v1/findings.json)."""
    data = _read_json(Path(path))
    if data is None:
        raise SystemExit(f"gallery file {path} not found")
    items = data.get("gallery") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise SystemExit(f'{path} has no "gallery" list')
    return [g for g in items if isinstance(g, dict) and g.get("task_id")]


def _gallery_entry(item: dict, scene: dict, summary: dict) -> dict | None:
    """One index.json gallery entry: the findings entry, the object ids to mark, the thumbnail,
    where the marked objects land on it, and every model's answer to the task."""
    idx, task = next(
        ((i, t) for i, t in enumerate(scene["tasks"]) if t["task_id"] == item["task_id"]),
        (None, None),
    )
    if task is None:
        return None
    camera = task.get("camera_id") or item.get("camera")
    cam = next((c for c in scene["cameras"] if c["camera_id"] == camera), None)
    objects = {o["id"]: o for o in scene["objects"]}
    truth_ids = task.get("truth_object_ids") or []
    shortcut_ids = task.get("shortcut_object_ids") or []
    marks = []
    for role, ids in (("truth", truth_ids), ("shortcut", shortcut_ids)):
        for oid in ids:
            obj = objects.get(oid)
            uv = project_uv(cam, obj.get("position")) if cam and obj else None
            if uv is not None:
                marks.append({"object_id": oid, "role": role, "name": obj["name"], "uv": uv})
    return {
        "task_id": task["task_id"],
        "scene_id": scene["scene_id"],
        "task_index": idx,
        "family": item.get("family") or task.get("family"),
        "camera": camera,
        "rig": task.get("rig"),
        "question": item.get("question") or task.get("question"),
        "truth": item.get("truth", task.get("answer_text")),
        "answer_type": task.get("answer_type"),
        "shortcut_answer": _compact(item.get("shortcut_answer", task.get("shortcut_answer"))),
        "models_taking_shortcut": list(item.get("models_taking_shortcut") or []),
        "truth_object_ids": truth_ids,
        "shortcut_object_ids": shortcut_ids,
        "thumb": (summary.get("thumbs") or {}).get(camera) or summary.get("thumb"),
        "marks": marks,
        "models": [{k: m.get(k) for k in GALLERY_MODEL_KEYS} for m in task["models"]],
    }


# ---------------------------------------------------------------- the whole export


def _n_scenes(run: Path, lay: layout.RunLayout) -> int:
    """Scenes in the run: from the freeze record when there is one (a frozen run can hold
    generated scenes that are not part of it), else every scene folder."""
    frozen = _read_json(run / "FROZEN.json") or {}
    status = frozen.get("scene_status")
    if isinstance(status, dict) and status:
        return int(sum(status.values()))
    return len(lay.scene_ids())


def _dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file())


def _prepare_out(out: Path) -> None:
    """Clear a previous export; refuse to clear a folder that is not one."""
    if out.exists() and any(out.iterdir()):
        if not (out / "index.json").exists():
            raise SystemExit(f"{out} is not empty and has no index.json; refusing to overwrite")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)


def export_explorer(
    run_dir: Path,
    out: Path,
    n_scenes: int = 6,
    max_width: int = 1024,
    max_mb: float = 40.0,
    results_dirs: Sequence[Path] | None = None,
    gallery: Path | None = None,
    compress_glbs: bool = False,
    glb_script: Path | None = None,
) -> dict:
    """Export a sample of `run_dir` for the Task Explorer into `out`. Returns the index dict.

    Scenes: every gallery scene (always), then `n_scenes` coverage scenes picked greedily from
    the rest; coverage scenes that would take the export past `max_mb` are skipped.
    """
    run = Path(run_dir)
    out = Path(out)
    tasks_by_scene = _load_tasks(run)
    if results_dirs:
        model_summaries, results = load_results_dirs(results_dirs)
    else:
        model_summaries, results = _load_models(run)
    models = {m["label"]: m for m in model_summaries}
    lay = layout.RunLayout(run)

    gallery_items = load_gallery(gallery) if gallery else []
    scene_of = {t["task_id"]: s for s, ts in tasks_by_scene.items() for t in ts}
    gallery_skipped: list[dict] = []
    gallery_scenes: dict[str, set[str]] = {}  # scene -> cameras needing a gallery thumbnail
    for item in gallery_items:
        sid = scene_of.get(item["task_id"])
        if sid is None:
            gallery_skipped.append({"task_id": item["task_id"], "reason": "not an accepted task"})
            continue
        if not _scene_layout(run, sid).render_manifest(sid).exists():
            gallery_skipped.append({"task_id": item["task_id"], "reason": "scene not rendered"})
            continue
        task = next(t for t in tasks_by_scene[sid] if t["task_id"] == item["task_id"])
        gallery_scenes.setdefault(sid, set()).add(task.get("camera_id") or item.get("camera"))

    rendered = {s for s in tasks_by_scene if lay.render_manifest(s).exists()}
    # A few spares, in case a scene is skipped for the size budget.
    order = select_scenes(
        tasks_by_scene, results, n_scenes + 8, eligible=rendered - set(gallery_scenes)
    )
    if not order and not gallery_scenes:
        raise SystemExit(f"no rendered scenes with accepted tasks in {run}")

    _prepare_out(out)
    budget = int(max_mb * 1024 * 1024)
    opts = {"models": models, "compress": compress_glbs, "glb_script": glb_script}
    gallery_summaries: list[dict] = []
    for sid, cams in gallery_scenes.items():
        tasks = tasks_by_scene[sid]
        summary = export_scene(run, sid, tasks, results, out, max_width, thumb_cameras=cams, **opts)
        gallery_summaries.append({**summary, "gallery": True})
    if gallery_scenes and _dir_bytes(out) > budget:
        print(f"explorer: the gallery scenes alone exceed {max_mb} MB")
    summaries: list[dict] = []
    skipped: list[str] = []
    for scene_id in order:
        if len(summaries) >= n_scenes:
            break
        summary = export_scene(
            run, scene_id, tasks_by_scene[scene_id], results, out, max_width, **opts
        )
        if _dir_bytes(out) > budget:
            shutil.rmtree(out / scene_id)
            skipped.append(scene_id)
            continue
        summaries.append({**summary, "gallery": False})
    summaries += gallery_summaries

    gallery_entries: list[dict] = []
    by_scene = {s["scene_id"]: s for s in summaries}
    for item in gallery_items:
        sid = scene_of.get(item["task_id"])
        if sid not in gallery_scenes:
            continue
        scene = _read_json(out / by_scene[sid]["path"]) or {}
        entry = _gallery_entry(item, scene, by_scene[sid])
        if entry is None:
            gallery_skipped.append({"task_id": item["task_id"], "reason": "task not in scene"})
            continue
        gallery_entries.append(entry)
    for g in gallery_skipped:
        print(f"explorer: gallery task {g['task_id']} skipped: {g['reason']}")

    run_info = _read_json(lay.run_file) or {}
    first = _read_json(out / summaries[0]["path"]) if summaries else {}
    index = {
        "explorer_version": EXPLORER_VERSION,
        "source": {
            "run": run.resolve().name,
            "run_version": run_info.get("run_version"),
            "recipe": run_info.get("recipe"),
            "run_seed": run_info.get("run_seed"),
            "generator_version": run_info.get("generator_version"),
            "spec_version": first.get("spec_version"),
            "n_scenes": _n_scenes(run, lay),
            "n_tasks": sum(len(v) for v in tasks_by_scene.values()),
        },
        "families": list(FAMILIES),
        "gates": list(GATE_IDS),
        "models": model_summaries,
        "scenes": summaries,
        "gallery": gallery_entries,
        "gallery_skipped": gallery_skipped,
        "skipped_for_size": skipped,
    }
    _write_json(out / "index.json", index)
    index["bytes"] = _dir_bytes(out)
    n_glb = sum(1 for s in summaries if s["gltf"])
    n_small = sum(1 for s in summaries if s["gltf_compressed"])
    print(
        f"explorer: {len(summaries)} scenes ({len(gallery_summaries)} for the gallery), "
        f"{sum(s['n_tasks'] for s in summaries)} tasks, {len(model_summaries)} model(s), "
        f"{len(gallery_entries)} gallery items, {n_small}/{n_glb} glb compressed, "
        f"{index['bytes'] / 1e6:.1f} MB -> {out}"
    )
    return index
