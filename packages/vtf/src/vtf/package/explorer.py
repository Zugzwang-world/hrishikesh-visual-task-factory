"""Task Explorer export: a small, self-contained sample of a run for the web viewer (web/).

    vtf export <run> --format explorer --out web/public/data --scenes 6

writes

    <out>/index.json                 run summary, models, and the exported scenes with summaries
    <out>/<scene_id>/scene.json      cameras (K, world_to_camera, rig, image), settled objects,
                                     table, gripper, zones, tasks with gates and model answers
    <out>/<scene_id>/<camera>.jpg    beauty image per camera, at most `max_width` px wide
    <out>/<scene_id>/thumb.jpg       small picker thumbnail
    <out>/<scene_id>/scene.glb       display glTF, copied from render/scene.glb when present

Everything is read from the run folder as plain JSON and tolerant of older contracts: a field a
v0.1 run does not have (rig, shortcut_conflict, question_alt, glTF, ...) is exported as null. The
export is a pure function of the run folder and the options (no timestamps), so re-exporting the
same run gives the same files.
"""

import json
import math
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image

from vtf.core import layout
from vtf.core.io import read_jsonl
from vtf.core.program import FAMILIES

EXPLORER_VERSION = "0.1.0"
GATE_IDS = ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8")
JPEG_QUALITY = 84
THUMB_WIDTH = 360
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
                parts = [f"{frac_key} {_scalar_text(weakest.get(frac_key))}"]
                for extra in ("visible_pixels", "pixels"):
                    if extra in weakest:
                        parts.append(f"{_scalar_text(weakest[extra])} px")
                objects.append([f"weakest of {len(rows)} ({weakest_id})", ", ".join(parts)])
    items = decision + objects + bools + numbers + flats + strings
    return items[:MAX_KEY_ITEMS]


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
            n = len(recs)
            correct = sum(1 for r in recs.values() if r.get("correct"))
            summaries.append(
                {
                    "label": label,
                    "model": next(iter(recs.values())).get("model"),
                    "with_image": next(iter(recs.values())).get("with_image", True),
                    "n": n,
                    "correct": correct,
                    "accuracy": _round(correct / n, 4) if n else None,
                    "prompt_version": summary_file.get("prompt_version"),
                    "settings": summary_file.get("settings"),
                }
            )
    return summaries, results


def _model_answer(label: str, rec: dict) -> dict:
    parsed = rec.get("parsed") if rec.get("parse_ok") else None
    if rec.get("parse_ok"):
        shown = parsed
    else:
        shown = (rec.get("raw_response") or rec.get("error") or "")[:MAX_RAW]
    return {
        "label": label,
        "model": rec.get("model"),
        "with_image": rec.get("with_image", True),
        "answer": _compact(shown),
        "parse_ok": bool(rec.get("parse_ok")),
        "correct": bool(rec.get("correct")),
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


def _task_entry(task: dict, cam_rigs: dict[str, str | None], results: dict) -> dict:
    models = [
        _model_answer(label, recs[task["task_id"]])
        for label, recs in sorted(results.items())
        if task["task_id"] in recs
    ]
    return {
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
        "models": models,
    }


def export_scene(
    run: Path, scene_id: str, tasks: list[dict], results: dict, out: Path, max_width: int
) -> dict:
    """Write <out>/<scene_id>/ and return its index.json summary."""
    lay = layout.RunLayout(run)
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

    gltf = None
    glb_name = manifest.get("gltf") or layout.GLTF
    if (render_dir / glb_name).exists():
        gltf = layout.GLTF
        shutil.copyfile(render_dir / glb_name, scene_out / gltf)

    meta = scene.get("meta", {})
    gripper = scene.get("gripper") or {}
    cam_rigs = {c["camera_id"]: c["rig"] for c in cameras}
    task_entries = [_task_entry(t, cam_rigs, results) for t in tasks]
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
        "n_tasks": len(task_entries),
        "n_objects": len(data["objects"]),
        "families": dict(sorted(Counter(t["family"] for t in task_entries).items())),
        "cameras": [{"camera_id": c["camera_id"], "rig": c["rig"]} for c in cameras],
        "gltf": gltf is not None,
        "shortcut_conflicts": sum(1 for t in task_entries if t["shortcut_conflict"]),
        "models": model_scores,
    }


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
    max_mb: float = 25.0,
) -> dict:
    """Export a sample of `run_dir` for the Task Explorer into `out`. Returns the index dict."""
    run = Path(run_dir)
    out = Path(out)
    tasks_by_scene = _load_tasks(run)
    model_summaries, results = _load_models(run)
    lay = layout.RunLayout(run)
    rendered = {s for s in tasks_by_scene if lay.render_manifest(s).exists()}
    # A few spares, in case a scene is skipped for the size budget.
    order = select_scenes(tasks_by_scene, results, n_scenes + 8, eligible=rendered)
    if not order:
        raise SystemExit(f"no rendered scenes with accepted tasks in {run}")

    _prepare_out(out)
    budget = int(max_mb * 1024 * 1024)
    summaries: list[dict] = []
    skipped: list[str] = []
    for scene_id in order:
        if len(summaries) >= n_scenes:
            break
        summary = export_scene(run, scene_id, tasks_by_scene[scene_id], results, out, max_width)
        if _dir_bytes(out) > budget:
            shutil.rmtree(out / scene_id)
            skipped.append(scene_id)
            continue
        summaries.append(summary)

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
            "n_scenes": len(lay.scene_ids()),
            "n_tasks": sum(len(v) for v in tasks_by_scene.values()),
        },
        "families": list(FAMILIES),
        "gates": list(GATE_IDS),
        "models": model_summaries,
        "scenes": summaries,
        "skipped_for_size": skipped,
    }
    _write_json(out / "index.json", index)
    index["bytes"] = _dir_bytes(out)
    print(
        f"explorer: {len(summaries)} scenes, "
        f"{sum(s['n_tasks'] for s in summaries)} tasks, "
        f"{len(model_summaries)} model(s), {index['bytes'] / 1e6:.1f} MB -> {out}"
    )
    return index
