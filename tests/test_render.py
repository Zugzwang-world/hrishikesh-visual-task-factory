"""Renderer checks against the core: camera matrices, index and depth versus the analytic ray
caster, visible pixels inside amodal masks, settle sanity, and re-render determinism.

The Blender tests are skipped when Blender or the cached HDRI is missing (CI has neither).
"""

import json
import stat
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from skimage.metrics import structural_similarity

from vtf.camera import preset, warp_index, warp_nearest
from vtf.core.geometry import Pose, look_at_camera_quat, quat_to_matrix, round_vec
from vtf.core.io import (
    read_depth,
    read_index_png,
    read_manifest,
    read_mask_png,
    read_rgb_png,
    read_scene,
    write_model,
)
from vtf.core.layout import RunLayout
from vtf.core.projection import intrinsics, world_to_camera
from vtf.core.raycast import raycast
from vtf.core.scene import GRIPPER_PASS_INDEX, TABLE_PASS_INDEX, CameraSpec
from vtf.render.bridge import find_blender, render_run, render_scene, render_scenes

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "spec" / "examples" / "scene_example.json"
FIXTURE = REPO / "spec" / "examples" / "run_example" / "scenes" / "scene_0000"
HDRI_META = REPO / "assets" / "cache" / "studio_small_09" / "meta.json"


def _blender() -> str | None:
    try:
        return find_blender()
    except FileNotFoundError:
        return None


BLENDER = _blender()
needs_blender = pytest.mark.skipif(
    BLENDER is None or not HDRI_META.exists(),
    reason="Blender or the cached studio_small_09 HDRI is not available",
)


def _write_scene(path: Path, resolution: tuple[int, int] | None = None, **meta) -> Path:
    spec = json.loads(EXAMPLE.read_text())
    if resolution:
        spec["cameras"][0]["resolution"] = list(resolution)
    spec["meta"].update(meta)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
    return path


@pytest.fixture(scope="module")
def renders(tmp_path_factory) -> dict[str, Path]:
    """The example scene rendered three times: twice at 320x240 (once through render_run on a
    run folder), once at 1024x768."""
    root = tmp_path_factory.mktemp("render")
    first = render_scene(EXAMPLE, root / "a")
    assert first.ok, f"{first.error}; see {first.log}"

    run = root / "run"
    _write_scene(RunLayout(run).scene_file("scene_0000"))
    report = render_run(run)
    assert report["counts"] == {"ok": 1, "failed": 0, "skipped": 0}, report

    big = render_scene(_write_scene(root / "big" / "scene.json", (1024, 768)), root / "big_out")
    assert big.ok, f"{big.error}; see {big.log}"
    return {"a": root / "a", "b": RunLayout(run).render_dir("scene_0000"), "big": root / "big_out",
            "run": run}  # fmt: skip


def _load(out: Path):
    manifest = read_manifest(out / "render_manifest.json")
    return manifest, read_scene(out / manifest.settled_scene_file)


@needs_blender
@pytest.mark.parametrize("which", ["a", "big"])
def test_manifest_and_camera_match_core(renders, which) -> None:
    manifest, settled = _load(renders[which])
    assert settled.meta.settled
    assert manifest.renderer == "blender" and manifest.data_passes_device == "CPU"
    for cam in manifest.cameras:
        spec = settled.camera(cam.camera_id)
        assert tuple(cam.resolution) == tuple(spec.resolution)
        # Blender stores lens and sensor width as float32: K agrees to about 1e-7 relative, which
        # is up to ~2e-5 px on a sampled focal length (the example's 28 mm is exact).
        np.testing.assert_allclose(cam.K, intrinsics(spec), rtol=1e-6, atol=1e-5)
        np.testing.assert_allclose(cam.world_to_camera, world_to_camera(spec), atol=1e-5)
        assert sorted(cam.amodal, key=int) == [str(o.pass_index) for o in settled.objects]


@needs_blender
@pytest.mark.parametrize("which", ["a", "big"])
def test_index_and_depth_match_raycast(renders, which) -> None:
    out = renders[which]
    manifest, settled = _load(out)
    for cam in manifest.cameras:
        index = read_index_png(out / cam.index)
        depth = read_depth(out / cam.depth)
        truth = raycast(settled, cam.camera_id)
        assert {TABLE_PASS_INDEX, GRIPPER_PASS_INDEX} <= set(np.unique(index).tolist())
        ious = {}
        for obj in settled.objects:
            a, b = index == obj.pass_index, truth.index == obj.pass_index
            ious[obj.id] = (a & b).sum() / max((a | b).sum(), 1)
        print(f"{which} IoU vs raycast:", {k: round(float(v), 4) for k, v in ious.items()})
        assert min(ious.values()) >= 0.98, ious
        agree = (index == truth.index) & (index > 0)
        err = np.abs(depth[agree] - truth.depth[agree])
        assert np.median(err) < 1e-4 and np.percentile(err, 99) < 5e-4, (
            np.median(err),
            np.percentile(err, 99),
        )
        assert np.all(depth[index > 0] > 0)  # every labelled pixel has a depth


@needs_blender
@pytest.mark.parametrize("which", ["a", "big"])
def test_visible_pixels_inside_amodal(renders, which) -> None:
    out = renders[which]
    manifest, settled = _load(out)
    for cam in manifest.cameras:
        index = read_index_png(out / cam.index)
        for obj in settled.objects:
            visible = index == obj.pass_index
            amodal = read_mask_png(out / cam.amodal[str(obj.pass_index)])
            assert visible.sum() > 0
            assert not (visible & ~amodal).any(), obj.id


@needs_blender
def test_settle_report(renders) -> None:
    manifest, settled = _load(renders["a"])
    s = manifest.settle
    assert s.off_table == [] and s.moving_at_end == []
    assert all(abs(gap) < 1e-3 for gap in s.support_gap_m.values()), s.support_gap_m
    assert 0.005 < s.max_displacement_m < 0.02  # objects spawn 1 cm above the table
    spawn = read_scene(EXAMPLE)
    for before, after in zip(spawn.objects, settled.objects, strict=True):
        assert after.pose.position[2] < before.pose.position[2]
        assert after.pose.rotation[0] >= 0


@needs_blender
def test_rerender_is_deterministic(renders) -> None:
    a, b = renders["a"], renders["b"]
    assert (a / "scene.settled.json").read_bytes() == (b / "scene.settled.json").read_bytes()
    manifest, _ = _load(a)
    for cam in manifest.cameras:
        assert np.array_equal(read_index_png(a / cam.index), read_index_png(b / cam.index))
        assert np.array_equal(read_depth(a / cam.depth), read_depth(b / cam.depth))
        for rel in cam.amodal.values():
            assert np.array_equal(read_mask_png(a / rel), read_mask_png(b / rel)), rel
        ssim = structural_similarity(
            read_rgb_png(a / cam.beauty), read_rgb_png(b / cam.beauty), channel_axis=2,
            data_range=255,
        )  # fmt: skip
        print(f"beauty SSIM re-render: {ssim:.6f}")
        assert ssim >= 0.999


@needs_blender
def test_render_run_skips_rendered_scenes(renders, capsys) -> None:
    report = render_run(renders["run"])
    assert report["counts"] == {"ok": 0, "failed": 0, "skipped": 1}
    assert "skipped" in capsys.readouterr().out
    saved = json.loads(RunLayout(renders["run"]).render_report.read_text())
    assert saved["scenes"]["scene_0000"]["status"] == "skipped"
    assert saved["scenes"]["scene_0000"]["timings"]["total"] > 0


@needs_blender
def test_failure_is_recorded(tmp_path) -> None:
    spec = json.loads(EXAMPLE.read_text())
    spec["objects"][0]["shape"] = "torus"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(spec))
    result = render_scene(bad, tmp_path / "out")
    assert not result.ok
    assert "torus" in result.error
    assert result.log.exists()
    assert not (tmp_path / "out" / "render_manifest.json").exists()


def test_find_blender_rejects_missing_path(monkeypatch, tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        find_blender(tmp_path / "no-blender")
    monkeypatch.setenv("VTF_BLENDER", str(tmp_path / "no-blender"))
    with pytest.raises(FileNotFoundError):
        find_blender()


def test_render_run_needs_scenes(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        render_run(tmp_path / "empty")


FAKE_BLENDER = """#!{python}
# Stands in for Blender: speaks render_scene.py's batch protocol. A scene whose path contains
# "slow" hangs; the others get the contract fixture's render folder.
import json, shutil, sys, time
args = sys.argv[sys.argv.index("--") + 1:]
jobs = json.load(open(args[args.index("--jobs") + 1]))
with open({launches!r}, "a") as f:
    f.write(str(len(jobs)) + "\\n")
print("fake blender starting", flush=True)
for n, job in enumerate(jobs):
    print(f"VTF_SCENE_START {{n}} {{json.dumps(job)}}", flush=True)
    if "slow" in job["scene"]:
        time.sleep(120)
    if "broken" in job["scene"]:
        print(f"VTF_RENDER_ERROR ValueError: broken scene", flush=True)
        print(f"VTF_SCENE_FAILED {{n}} ValueError: broken scene", flush=True)
        continue
    shutil.copytree({fixture!r}, job["out"], dirs_exist_ok=True)
    print(f"VTF_SCENE_DONE {{n}} {{{{}}}}", flush=True)
"""


def test_batches_timeout_and_restart(tmp_path) -> None:
    """One process per batch; a hung scene is killed at the timeout, marked failed with no
    manifest, and Blender restarts for the rest of its batch."""
    launches = tmp_path / "launches.txt"
    fake = tmp_path / "fake_blender"
    fake.write_text(
        FAKE_BLENDER.format(
            python=sys.executable, launches=str(launches), fixture=str(FIXTURE / "render")
        )
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    names = ["a", "slow", "b", "broken", "c"]
    scenes, outs = [], []
    for name in names:
        scene = tmp_path / name / "scene.json"
        scene.parent.mkdir()
        scene.write_bytes((FIXTURE / "scene.json").read_bytes())
        scenes.append(scene)
        outs.append(tmp_path / name / "render")
    stale = outs[1] / "render_manifest.json"  # a stale manifest must not survive a failure
    stale.parent.mkdir()
    stale.write_text("{}")
    seen = []
    start = time.perf_counter()
    results = render_scenes(
        scenes, outs, batch_size=3, timeout_per_scene=3, blender=fake,
        on_result=lambda i, r: seen.append(i),
    )  # fmt: skip
    elapsed = time.perf_counter() - start
    assert [r.ok for r in results] == [True, False, True, False, True]
    assert results[1].timed_out and "timed out" in results[1].error
    assert "broken scene" in results[3].error and not results[3].timed_out
    assert not stale.exists() and not (outs[3] / "render_manifest.json").exists()
    for i in (0, 2, 4):
        assert read_manifest(results[i].manifest).scene_id == "scene_0000"
        assert results[i].log.exists()
    assert "VTF_SCENE_START" in results[1].log.read_text()
    # batch [a, slow, b] is killed at slow; a new process runs [b, broken, c]
    assert launches.read_text().split() == ["3", "3"]
    assert sorted(seen) == list(range(5))
    assert elapsed < 60, elapsed


def test_missing_blender_fails_every_scene(tmp_path) -> None:
    results = render_scenes(
        [FIXTURE / "scene.json"], [tmp_path / "out"], blender=tmp_path / "no-blender"
    )
    assert not results[0].ok and "not found" in results[0].error
    assert not (tmp_path / "out" / "render_manifest.json").exists()


def _three_camera_scene(path: Path, levels: dict[str, str], samples: int = 16) -> Path:
    """The example scene with overhead and gripper cameras added and a dial level per camera."""
    spec = read_scene(EXAMPLE)
    top = spec.table.height
    eye = (0.02, -0.04, top + 1.1)
    overhead = CameraSpec(
        id="overhead",
        rig="overhead",
        pose=Pose(
            position=round_vec(eye),
            rotation=round_vec(look_at_camera_quat(eye, (0.02, 0.0, top), (0, 1, 0))),
        ),
        focal_mm=26.0,
        resolution=(240, 180),
    )
    tip = spec.gripper.tip
    rot = quat_to_matrix(tip.rotation)
    pos = np.asarray(tip.position) + rot @ np.array([0.0, 0.05, spec.gripper.finger_length + 0.03])
    target = np.asarray(tip.position) + rot @ np.array([0.0, 0.0, -0.3])
    gripper = CameraSpec(
        id="gripper",
        rig="gripper",
        pose=Pose(
            position=round_vec(pos),
            rotation=round_vec(look_at_camera_quat(pos, target, rot @ np.array([0.0, 1.0, 0.0]))),
        ),
        focal_mm=16.0,
        resolution=(200, 150),
    )
    cams = [
        cam.model_copy(update={"quality": preset(levels.get(cam.id, "clean"), seed=i + 1)})
        for i, cam in enumerate([spec.cameras[0], overhead, gripper])
    ]
    render = spec.render.model_copy(update={"samples": samples})
    write_model(path, spec.model_copy(update={"cameras": cams, "render": render}))
    return path


@pytest.fixture(scope="module")
def multicam(tmp_path_factory) -> dict:
    """Two three-camera scenes in one batched Blender call: one with mild, harsh and cctv dials,
    one clean."""
    root = tmp_path_factory.mktemp("multicam")
    dial = _three_camera_scene(
        root / "dial" / "scene.json", {"front": "mild", "overhead": "harsh", "gripper": "cctv"}
    )
    clean = _three_camera_scene(root / "clean" / "scene.json", {})
    outs = [root / "dial" / "render", root / "clean" / "render"]
    results = render_scenes([dial, clean], outs, batch_size=5)
    for r in results:
        assert r.ok, f"{r.error}; see {r.log}"
    return {"results": results, "dial": outs[0], "clean": outs[1]}


@needs_blender
def test_multicam_one_blender_process(multicam) -> None:
    first, second = multicam["results"]
    assert first.startup_seconds > 0 and second.startup_seconds == 0  # one process, two scenes
    for which in ("dial", "clean"):
        manifest, settled = _load(multicam[which])
        assert [c.camera_id for c in manifest.cameras] == ["front", "overhead", "gripper"]
        for cam in manifest.cameras:
            spec = settled.camera(cam.camera_id)
            assert cam.rig == spec.rig and tuple(cam.resolution) == tuple(spec.resolution)
            np.testing.assert_allclose(cam.K, intrinsics(spec), rtol=1e-6, atol=1e-5)
            np.testing.assert_allclose(cam.world_to_camera, world_to_camera(spec), atol=1e-5)
            expected = {} if which == "clean" else spec.quality.model_dump(mode="json")
            assert cam.quality == expected
            assert (cam.beauty_raw is None) == (which == "clean")


@needs_blender
@pytest.mark.parametrize("which", ["dial", "clean"])
def test_multicam_passes_align_under_distortion(multicam, which) -> None:
    """Index, depth and amodal masks match the analytic ray cast warped with the same dial."""
    out = multicam[which]
    manifest, settled = _load(out)
    for cam in manifest.cameras:
        q = settled.camera(cam.camera_id).quality
        k = np.asarray(cam.K)
        index = read_index_png(out / cam.index)
        depth = read_depth(out / cam.depth)
        truth = raycast(settled, cam.camera_id)
        t_index = warp_index(truth.index, q, k)
        t_depth = warp_nearest(truth.depth, q, k, 0.0)
        ious = {}
        for obj in settled.objects:
            a, b = index == obj.pass_index, t_index == obj.pass_index
            if (a | b).sum() >= 30:
                ious[obj.id] = (a & b).sum() / (a | b).sum()
            visible = index == obj.pass_index
            amodal = read_mask_png(out / cam.amodal[str(obj.pass_index)])
            assert not (visible & ~amodal).any(), (cam.camera_id, obj.id)
        assert ious, cam.camera_id  # every camera sees some object
        print(which, cam.camera_id, {k: round(float(v), 4) for k, v in ious.items()})
        assert min(ious.values()) >= 0.95, (cam.camera_id, ious)
        agree = (index == t_index) & (index > 0)
        assert np.median(np.abs(depth[agree] - t_depth[agree])) < 1e-4
        assert np.all(depth[index > 0] > 0)


@needs_blender
def test_gltf_export(multicam) -> None:
    import trimesh

    manifest, settled = _load(multicam["dial"])
    assert manifest.gltf == "scene.glb"
    path = multicam["dial"] / manifest.gltf
    assert 0 < path.stat().st_size < 3_000_000
    names = set(trimesh.load(path).geometry)
    assert {o.id for o in settled.objects} <= names and "table" in names
