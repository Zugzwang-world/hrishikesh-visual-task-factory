"""`vtf twin register <inbox>`: turn photos of the physical twins into twin tasks.

    uv run vtf twin register twins/inbox --selection docs/twins/selection.json \
        --twins out/twins_digital --out out/twins_real

For every photo in the inbox (JPEG, PNG or HEIC; any name):

1. Pose. The mat's markers give the phone's pose (vtf.twin.register.photo_pose).
2. Which layout and which spot. From the file name when it starts with L01..L12 or H1..H3
   (and names a spot: L03_side.jpg, H1_front_2.jpg), else recognised: the layout whose objects
   best explain the not-mat pixels, and the photo spot nearest the estimated lens position.
3. Gate G8 against that layout's settled scene (the rendered digital twin is the truth). An
   overlay per photo goes to <out>/overlays/.
4. Sibling scene. Per layout, the digital-twin scene with one camera per accepted photo at the
   photo's estimated pose and field of view: <out>/sim/scenes/<scene_id>/scene.json.
5. Render the siblings in Blender and validate them (gates G1-G4, G7) like any run: <out>/sim is
   a normal run; its tasks are the synthetic siblings.
6. Real tasks. Each sibling task gets a twin with the photo (at the camera's resolution) as its
   image, the same question and answer, the sibling's gates plus G8: <out>/real is a run with
   tasks/ and manifest.jsonl, so `vtf eval` works on it unchanged. <out>/pairs.jsonl pairs every
   real task with its synthetic sibling (the input of `vtf twin sim-to-real`).

Re-running is cheap: renders are reused while the sibling scene file is unchanged.
"""

import json
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from vtf.core.hashing import sha256_json, short_id
from vtf.core.io import link_or_copy, read_jsonl, read_scene, read_task, write_jsonl, write_model
from vtf.core.layout import RunLayout
from vtf.core.scene import SceneSpec
from vtf.core.task import GateResult, ManifestRow
from vtf.package.writer import manifest_row
from vtf.twin.digital import PHOTO_SPOTS, SPOT_IDS, layout_label, spot, twin_scene
from vtf.twin.register import (
    G8Result,
    layout_match,
    overlay,
    photo_pose,
    run_g8,
    working_photo,
)

PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
NAME_RE = re.compile(r"^(L\d{2}|H\d)(?:[_\- ]+([A-Za-z]+))?", re.IGNORECASE)
REGISTRATION = "registration.jsonl"
PAIRS = "pairs.jsonl"
SUMMARY = "summary.json"
MIN_MATCH = 0.3  # recognised layout: not-mat vs objects IoU at least this
MIN_MATCH_MARGIN = 0.1  # and this far ahead of the runner-up


@dataclass
class Layout:
    label: str
    scene_id: str
    scene: SceneSpec  # the digital twin's scene file (sampled)
    settled: SceneSpec  # its settled poses: the truth G8 checks against


def _layout(run: RunLayout, label: str, sid: str) -> Layout:
    settled_path = run.settled_scene(sid)
    if not settled_path.exists():
        raise FileNotFoundError(
            f"{settled_path} missing: render the digital twins first (vtf twin digital, "
            "vtf render)"
        )
    return Layout(label, sid, read_scene(run.scene_file(sid)), read_scene(settled_path))


def load_layouts(selection: dict, twins_run: Path) -> list[Layout]:
    """L01.. from the selection's scene_ids in `twins_run`, then the hero layouts H1.. (brief
    VTF-08: front-spot layouts built to show the 2D-shortcut trap) from their own digital-twin
    run, selection["hero"]["twins_run"]."""
    run = RunLayout(twins_run)
    out = [_layout(run, layout_label(i), sid) for i, sid in enumerate(selection["scene_ids"])]
    hero = selection.get("hero") or {}
    if hero.get("layouts"):
        hrun = RunLayout(Path(hero["twins_run"]))
        out += [_layout(hrun, h["label"], h["scene_id"]) for h in hero["layouts"]]
    return out


def readable(path: Path, tmp: Path) -> Path:
    """HEIC photos are converted to JPEG with macOS sips (EXIF kept); others are used as is."""
    if path.suffix.lower() not in (".heic", ".heif"):
        return path
    out = tmp / (path.stem + ".jpg")
    exe = shutil.which("sips")
    if exe is None:
        raise RuntimeError(f"{path.name}: HEIC needs macOS sips; save photos as JPEG instead")
    subprocess.run([exe, "-s", "format", "jpeg", str(path), "--out", str(out)], check=True,
                   capture_output=True)  # fmt: skip
    return out


def nearest_spot(position: list[float], table_height: float) -> tuple[str, float]:
    best = min(
        PHOTO_SPOTS, key=lambda s: float(np.linalg.norm(np.subtract(position, s.eye(table_height))))
    )
    return best.id, float(np.linalg.norm(np.subtract(position, best.eye(table_height))))


def identify(image, pose, layouts: list[Layout]) -> tuple[Layout | None, list[tuple[str, float]]]:
    """The layout the photo shows, by how well each layout's objects explain the photo."""
    work, cam = working_photo(image, pose, "probe", "front")
    scores = sorted(
        ((lay.label, layout_match(work, cam, lay.settled)) for lay in layouts),
        key=lambda x: -x[1],
    )
    best = scores[0]
    runner = scores[1][1] if len(scores) > 1 else 0.0
    if best[1] < MIN_MATCH or best[1] - runner < MIN_MATCH_MARGIN:
        return None, scores[:3]
    return next(lay for lay in layouts if lay.label == best[0]), scores[:3]


def register_inbox(
    inbox: Path,
    selection: dict,
    twins_run: Path,
    out: Path,
    render: bool = True,
    device: str = "gpu",
    focal_35mm: float | None = None,
) -> dict:
    inbox, out = Path(inbox), Path(out)
    layouts = load_layouts(selection, twins_run)
    by_label = {lay.label: lay for lay in layouts}
    table_h = layouts[0].settled.table.height
    photos = sorted(p for p in inbox.iterdir() if p.suffix.lower() in PHOTO_SUFFIXES)
    if not photos:
        raise FileNotFoundError(f"no photos (jpg, png, heic) in {inbox}")
    (out / "overlays").mkdir(parents=True, exist_ok=True)
    images_dir = out / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    rows, accepted = [], defaultdict(list)  # label -> [(camera, photo row)]
    used_ids: dict[str, set[str]] = defaultdict(set)
    with tempfile.TemporaryDirectory() as tmp:
        for photo in photos:
            row = {"photo": photo.name}
            try:
                path = readable(photo, Path(tmp))
            except Exception as exc:
                rows.append({**row, "passed": False, "reason": f"unreadable: {exc}"})
                print(f"  {photo.name}: unreadable ({exc})", flush=True)
                continue
            image, pose = photo_pose(path, table_h, focal_35mm)
            if not pose.ok:
                rows.append({**row, "passed": False, "reason": f"pose: {pose.reason}",
                             "pose": pose.model_dump(mode="json")})  # fmt: skip
                print(f"  {photo.name}: REJECT pose: {pose.reason}", flush=True)
                continue
            m = NAME_RE.match(photo.stem)
            lay, scores, named_spot = None, [], None
            if m and m.group(1).upper() in by_label:
                lay = by_label[m.group(1).upper()]
                if m.group(2) and m.group(2).lower() in SPOT_IDS:
                    named_spot = m.group(2).lower()
                row["identified_by"] = "name"
            else:
                lay, scores = identify(image, pose, layouts)
                row["identified_by"] = "recognised"
                row["layout_scores"] = [[lab, round(s, 3)] for lab, s in scores]
            if lay is None:
                rows.append({**row, "passed": False,
                             "reason": "layout not recognised; name the file L01_front.jpg etc."})
                print(f"  {photo.name}: REJECT layout not recognised {scores}", flush=True)
                continue
            spot_id, dist = nearest_spot(pose.camera_position, table_h)
            spot_id = named_spot or spot_id
            cam_id, n = spot_id, 2
            while cam_id in used_ids[lay.label]:
                cam_id, n = f"{spot_id}_{n}", n + 1
            result, work = run_g8(path, lay.settled, cam_id, spot(spot_id).rig,
                                  loaded=(image, pose))  # fmt: skip
            result = result.model_copy(update={"photo": photo.name})
            tag = f"{lay.label}_{cam_id}"
            row.update(label=lay.label, scene_id=lay.scene_id, camera_id=cam_id,
                       spot_distance_m=round(dist, 3), passed=result.passed,
                       reason=result.reason, g8=result.model_dump(mode="json"))  # fmt: skip
            if work is not None:
                cv2.imwrite(str(out / "overlays" / f"{tag}.jpg"),
                            overlay(work, result, lay.settled, f"{tag} ({photo.name})"),
                            [cv2.IMWRITE_JPEG_QUALITY, 85])  # fmt: skip
            if result.passed:
                used_ids[lay.label].add(cam_id)
                cv2.imwrite(str(images_dir / f"{tag}.png"), work)
                accepted[lay.label].append((result, tag))
            rows.append(row)
            print(f"  {photo.name}: {tag} {'pass' if result.passed else 'REJECT'} "
                  f"{result.reason}", flush=True)  # fmt: skip
    write_jsonl(out / REGISTRATION, rows)

    # Sibling scenes: one per layout with an accepted photo, a camera per photo.
    sim = RunLayout(out / "sim")
    for label, items in accepted.items():
        lay = by_label[label]
        cams = [r.camera for r, _ in items]
        scene = twin_scene(lay.scene, cams)  # type: ignore[arg-type]
        path = sim.scene_file(lay.scene_id)
        text = scene.model_dump_json()
        if not path.exists() or read_scene(path).model_dump_json() != text:
            write_model(path, scene)
    summary = {
        "photos": len(photos),
        "accepted": sum(len(v) for v in accepted.values()),
        "rejected": [{"photo": r["photo"], "reason": r["reason"]} for r in rows
                     if not r.get("passed")],  # fmt: skip
        "layouts_with_photos": sorted(accepted),
    }
    if accepted and render:
        from vtf.render.bridge import render_run
        from vtf.validate.pipeline import validate_run

        render_run(sim.root, device=device, gltf=False, keep_raw=False,
                   batch_size=max(1, len(accepted)))  # fmt: skip
        validate_run(sim.root, twins=False)
    if accepted and sim.task_manifest.exists():  # --no-render re-packages earlier siblings
        summary.update(package_real_tasks(out, accepted, by_label))
    (out / SUMMARY).write_text(json.dumps(summary, indent=2) + "\n")
    print(f"vtf twin register: {summary['accepted']} of {summary['photos']} photos pass G8; "
          f"{summary.get('real_tasks', 0)} real tasks paired with synthetic siblings -> {out}")
    return summary


def real_task_id(sim_task_id: str, photo_sha256: str) -> str:
    return short_id("t", sha256_json({"sim_task": sim_task_id, "photo": photo_sha256}))


def package_real_tasks(out: Path, accepted: dict, by_label: dict[str, Layout]) -> dict:
    """Pair every accepted sibling task with its photo: <out>/real/tasks and pairs.jsonl."""
    sim = RunLayout(out / "sim")
    real = RunLayout(out / "real")
    if real.tasks_dir.exists():
        shutil.rmtree(real.tasks_dir)
    photo_of: dict[tuple[str, str], tuple[G8Result, str]] = {}
    for label, items in accepted.items():
        for result, tag in items:
            photo_of[(by_label[label].scene_id, result.camera_id)] = (result, tag)
    label_of = {lay.scene_id: lay.label for lay in by_label.values()}
    rows: list[ManifestRow] = []
    pairs = []
    settle_diff = {}
    for sim_row in read_jsonl(sim.task_manifest) if sim.task_manifest.exists() else []:
        key = (sim_row["scene_id"], sim_row["camera_id"])
        if key not in photo_of:
            continue
        result, tag = photo_of[key]
        task = read_task(sim.root / sim_row["path"])
        gates = dict(task.gates)
        gates["G8"] = GateResult(passed=True, evidence=_g8_evidence(result))
        difficulty = dict(task.difficulty)
        difficulty["image_source"] = "photo"
        twin = task.model_copy(update={
            "task_id": real_task_id(task.task_id, result.photo_sha256),
            "gates": gates, "difficulty": difficulty,
        })  # fmt: skip
        tdir = real.task_dir(twin.task_id)
        tdir.mkdir(parents=True, exist_ok=True)
        link_or_copy(out / "images" / f"{tag}.png", tdir / "image.png")  # hard link: no disk
        write_model(tdir / "task.json", twin)
        rows.append(manifest_row(twin))
        pairs.append({
            "real_task_id": twin.task_id, "sim_task_id": task.task_id,
            "layout": label_of[task.scene_id], "scene_id": task.scene_id,
            "camera_id": task.camera_id, "family": task.family, "photo": result.photo,
        })  # fmt: skip
    for sid in {k[0] for k in photo_of}:
        settle_diff[sid] = _settle_difference(sim, sid, by_label[label_of[sid]].settled)
    write_jsonl(real.task_manifest, rows)
    write_jsonl(out / PAIRS, pairs)
    return {"real_tasks": len(rows), "settle_difference_mm": settle_diff}


def _g8_evidence(result: G8Result) -> dict:
    return {
        "photo": result.photo,
        "photo_sha256": result.photo_sha256,
        "markers": result.pose.marker_ids,
        "reprojection_rms_px": result.pose.reprojection_rms_px,
        "focal_source": result.pose.focal_source,
        "camera_position": result.pose.camera_position,
        "objects": [o.model_dump(mode="json") for o in result.objects],
        "tolerances": result.tolerances,
        "g8_version": result.g8_version,
    }


def _settle_difference(sim: RunLayout, scene_id: str, truth: SceneSpec) -> float | None:
    """Largest object position difference (mm) between the sibling's settle and the digital
    twin's (the poses G8 checked against). Same objects, same physics: expected 0."""
    path = sim.settled_scene(scene_id)
    if not path.exists():
        return None
    sib = read_scene(path)
    diffs = [
        float(np.linalg.norm(np.subtract(sib.object(o.id).pose.position, o.pose.position)))
        for o in truth.objects
    ]
    return round(max(diffs) * 1000, 3)
