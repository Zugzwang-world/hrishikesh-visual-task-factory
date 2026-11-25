"""Physical twins, VTF-07: mat texture, digital-twin scenes, gate G8 (pose and placement) on
synthetic photos, the photo inbox pipeline (without Blender), the guide and the sim-to-real
statistics. Synthetic photos are made core-side: the mat texture warped into the camera by the
true pose, objects painted from the mesh ray caster."""

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from vtf.core.io import read_scene, write_model
from vtf.core.layout import RunLayout
from vtf.core.scene import SceneSpec
from vtf.twin import register as g8
from vtf.twin.digital import (
    PHOTO_SPOTS,
    TWIN_MAT_MATERIAL,
    TWIN_SETTLE_FRAMES,
    default_texture_path,
    digital_twin_scene,
    misplaced_scene,
    pick_misplacement,
    texture_to_mat,
)
from vtf.twin.mat import default_mat_spec

REPO = Path(__file__).resolve().parents[1]
TWIN_RECIPE = REPO / "recipes" / "physical_twin.yaml"


@pytest.fixture(scope="module")
def twin_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from vtf.scene.generate import generate_run

    out = tmp_path_factory.mktemp("twins_g8")
    generate_run(recipe=TWIN_RECIPE, n=4, seed=19, out=out)
    return out


def _coloured(scene: SceneSpec) -> list[str]:
    return [
        o.id
        for o in scene.objects
        if o.placement.kind == "table" and o.color_name not in g8.LOW_CONTRAST_COLOURS
    ]


@pytest.fixture(scope="module")
def layout_scene(twin_run: Path) -> SceneSpec:
    """A digital-twin scene with at least one coloured object standing on the table."""
    run = RunLayout(twin_run)
    for sid in run.scene_ids():
        scene = digital_twin_scene(read_scene(run.scene_file(sid)))
        if _coloured(scene):
            return scene
    pytest.skip("no scene with a coloured object")


def synthetic_photo(scene: SceneSpec, camera_id: str) -> np.ndarray:
    from vtf.core.raycast import raycast

    cam = scene.camera(camera_id)
    rc = raycast(scene, camera_id)
    k, rot, t = g8.camera_matrices(cam)
    width, height = cam.resolution
    tex = cv2.imread(str(default_texture_path()))
    h = g8.mat_homography(k, rot, t, scene.table.height) @ texture_to_mat()
    img = cv2.warpPerspective(tex, h, (width, height), flags=cv2.INTER_AREA).astype(np.float32)
    img *= (0.8 + 0.15 * np.arange(width) / width)[None, :, None]  # uneven light
    obj = (rc.index > 0) & (rc.index < 1000)
    colour = (np.clip(rc.colour, 0, 1) ** (1 / 2.2) * 255)[..., ::-1]
    img[obj] = colour[obj] * 0.9
    img[rc.index == 0] = 70
    return np.clip(img, 0, 255).astype(np.uint8)


# Mat texture and digital-twin scenes -----------------------------------------------------------


def test_mat_texture_markers_land_on_the_mat_spec() -> None:
    tex = cv2.imread(str(default_texture_path()))
    assert tex.shape == (1120, 1600, 3)  # 40 x 28 cm at 4 px/mm
    found = g8.detect_markers(tex)
    spec = default_mat_spec()
    assert sorted(found) == [m.id for m in spec.markers]
    for m in spec.markers:
        got = g8.map_points(texture_to_mat(), found[m.id])
        assert np.abs(got - np.asarray(m.corners_xy)).max() < 0.0003  # 0.3 mm


def test_digital_twin_scene_changes_only_mat_gripper_cameras(twin_run: Path) -> None:
    run = RunLayout(twin_run)
    sid = run.scene_ids()[0]
    src = read_scene(run.scene_file(sid))
    twin = digital_twin_scene(src)
    assert twin.objects == src.objects and twin.lighting == src.lighting
    assert twin.meta.seed == src.meta.seed and twin.meta.scene_id == sid
    assert twin.table.material_id == TWIN_MAT_MATERIAL and twin.gripper.parts == []
    assert twin.physics.settle_frames == TWIN_SETTLE_FRAMES
    assert [c.id for c in twin.cameras] == [s.id for s in PHOTO_SPOTS]
    for cam in src.cameras:  # the recipe's fixed spots are the guide's front and overhead
        assert twin.camera(cam.id).pose == cam.pose
        assert twin.camera(cam.id).focal_mm == cam.focal_mm
    from vtf.core.projection import project

    corners = np.array(
        [(x, y, twin.table.height) for m in default_mat_spec().markers for x, y in m.corners_xy]
    )
    for cam in twin.cameras:  # every marker is in every view
        uv, z = project(cam, corners)
        w, h = cam.resolution
        assert (z > 0).all() and uv.min() > 20 and (uv[:, 0] < w - 20).all()
        assert (uv[:, 1] < h - 20).all()


def test_misplacement_stays_on_the_mat_and_clear(twin_run: Path) -> None:
    run = RunLayout(twin_run)
    scene = read_scene(run.scene_file(run.scene_ids()[0]))
    pick = pick_misplacement(scene)
    assert pick is not None and pick == pick_misplacement(scene)  # deterministic
    oid, shift = pick
    moved = misplaced_scene(scene, oid, shift)
    before, after = scene.object(oid).pose.position, moved.object(oid).pose.position
    assert np.linalg.norm(np.subtract(after, before)) == pytest.approx(0.04, abs=1e-6)
    area = default_mat_spec().placement_area
    assert area.contains(after[0], after[1])


# Gate G8 ---------------------------------------------------------------------------------------


def test_g8_passes_a_correct_photo_and_rejects_a_moved_object(
    layout_scene: SceneSpec, tmp_path: Path
) -> None:
    scene = layout_scene
    cam = scene.camera("side")
    photo = tmp_path / "side.png"
    cv2.imwrite(str(photo), synthetic_photo(scene, "side"))
    result, work = g8.run_g8(photo, scene, "side", "front", truth=cam)
    assert result.pose.ok and len(result.pose.marker_ids) >= 3
    assert result.pose.reprojection_rms_px < 1.5
    assert result.pose_error["position_mm"] < 5 and result.pose_error["rotation_deg"] < 0.5
    assert work is not None and work.shape[:2] == (768, 1024)
    measured = [o for o in result.objects if o.measured]
    assert measured and all(o.error_mm <= 5 for o in measured), result.objects
    assert result.passed, result.reason

    target = _coloured(scene)[0]
    moved = None
    for d in ((0.04, 0.0), (-0.04, 0.0), (0.0, 0.04), (0.0, -0.04)):
        candidate = misplaced_scene(scene, target, d)
        x, y, _ = candidate.object(target).pose.position
        if default_mat_spec().placement_area.contains(x, y):
            moved = candidate
            break
    assert moved is not None
    bad = tmp_path / "moved.png"
    cv2.imwrite(str(bad), synthetic_photo(moved, "side"))
    result2, _ = g8.run_g8(bad, scene, "side", "front", truth=cam)
    assert not result2.passed
    rec = next(o for o in result2.objects if o.object_id == target)
    assert rec.measured and not rec.ok and rec.error_mm == pytest.approx(40, abs=6)


def test_g8_fails_cleanly_without_markers(layout_scene: SceneSpec, tmp_path: Path) -> None:
    blank = tmp_path / "blank.png"
    cv2.imwrite(str(blank), np.full((768, 1024, 3), 200, np.uint8))
    result, work = g8.run_g8(blank, layout_scene, "front", "front")
    assert not result.passed and work is None and "markers" in result.reason


def test_pose_camera_round_trip(layout_scene: SceneSpec, tmp_path: Path) -> None:
    cam = layout_scene.camera("front")
    img = synthetic_photo(layout_scene, "front")
    k = g8.photo_intrinsics(1024, 768)
    pose = g8.estimate_pose(img, k, layout_scene.table.height)
    est = g8.pose_camera(pose, "front", "front", (1024, 768))
    err = g8.pose_error(est, cam)
    assert err["position_mm"] < 5 and err["rotation_deg"] < 0.5 and err["focal_mm"] == 0


# Inbox pipeline (no Blender) -------------------------------------------------------------------


def test_register_inbox_names_recognises_and_writes_siblings(
    twin_run: Path, tmp_path: Path
) -> None:
    from vtf.twin.real import load_layouts, register_inbox

    src = RunLayout(twin_run)
    ids = [s for s in src.scene_ids() if _coloured(read_scene(src.scene_file(s)))][:2]
    selection = {"scene_ids": ids}
    digital = RunLayout(tmp_path / "digital")
    for sid in ids:  # stand-in for rendered twins: the sampled scene as its own settle
        scene = digital_twin_scene(read_scene(src.scene_file(sid)))
        write_model(digital.scene_file(sid), scene)
        write_model(digital.settled_scene(sid), scene)
    layouts = load_layouts(selection, digital.root)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    cv2.imwrite(str(inbox / "L01_overhead.png"), synthetic_photo(layouts[0].scene, "overhead"))
    cv2.imwrite(str(inbox / "IMG_0002.png"), synthetic_photo(layouts[1].scene, "side"))
    out = tmp_path / "real"
    summary = register_inbox(inbox, selection, digital.root, out, render=False)
    assert summary["photos"] == 2 and summary["accepted"] == 2, summary
    rows = [json.loads(x) for x in (out / "registration.jsonl").read_text().splitlines()]
    by_photo = {r["photo"]: r for r in rows}
    assert by_photo["L01_overhead.png"]["identified_by"] == "name"
    assert by_photo["L01_overhead.png"]["camera_id"] == "overhead"
    assert by_photo["IMG_0002.png"]["identified_by"] == "recognised"
    assert by_photo["IMG_0002.png"]["label"] == "L02"
    assert by_photo["IMG_0002.png"]["camera_id"] == "side"
    sim = RunLayout(out / "sim")
    sib = read_scene(sim.scene_file(ids[1]))
    assert [c.id for c in sib.cameras] == ["side"]
    err = g8.pose_error(sib.camera("side"), layouts[1].scene.camera("side"))
    assert err["position_mm"] < 5
    assert (out / "overlays" / "L02_side.jpg").exists()


# Guide and sim-to-real -------------------------------------------------------------------------


def test_guide_writes_markdown_and_pdf(twin_run: Path, tmp_path: Path) -> None:
    from vtf.twin.guide import make_guide

    ids = RunLayout(twin_run).scene_ids()[:2]
    md, pdf = make_guide(twin_run, {"scene_ids": ids}, tmp_path)
    text = md.read_text()
    assert "twins/inbox/" in text and "L01" in text and "L02" in text
    assert all(s.id.capitalize() in text for s in PHOTO_SPOTS)
    assert pdf.read_bytes()[:4] == b"%PDF"
    again = make_guide(twin_run, {"scene_ids": ids}, tmp_path / "again")
    assert again[1].read_bytes() == pdf.read_bytes()


def test_sim_to_real_statistics() -> None:
    from vtf.twin.sim_to_real import compare, mcnemar_p

    pairs = [
        {"real_task_id": f"r{i}", "sim_task_id": f"s{i}", "layout": f"L0{i % 3}",
         "family": "count" if i % 2 else "relation"}
        for i in range(12)
    ]  # fmt: skip
    real = {f"r{i}": i not in (0, 1, 2) for i in range(12)}  # 9 of 12
    sim = {f"s{i}": i != 5 for i in range(12)}  # 11 of 12
    s = compare(pairs, real, sim)
    assert s["pairs"] == 12 and s["layouts"] == 3
    assert s["accuracy_real"] == 0.75 and s["accuracy_sim"] == pytest.approx(11 / 12, abs=1e-4)
    assert s["photo_right_render_wrong"] == 1 and s["render_right_photo_wrong"] == 3
    assert s["mcnemar_p"] == mcnemar_p(1, 3) == 0.625
    lo, hi = s["ci95"]["difference"]
    assert lo <= s["difference"] <= hi
    assert set(s["families"]) == {"count", "relation"}
    assert compare(pairs, real, sim) == s  # seeded
