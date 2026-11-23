"""Renderer checks against the core: camera matrices, index and depth versus the analytic ray
caster, visible pixels inside amodal masks, settle sanity, and re-render determinism.

The Blender tests are skipped when Blender or the cached HDRI is missing (CI has neither).
"""

import json
from pathlib import Path

import numpy as np
import pytest
from skimage.metrics import structural_similarity

from vtf.core.io import (
    read_depth,
    read_index_png,
    read_manifest,
    read_mask_png,
    read_rgb_png,
    read_scene,
)
from vtf.core.layout import RunLayout
from vtf.core.projection import intrinsics, world_to_camera
from vtf.core.raycast import raycast
from vtf.core.scene import GRIPPER_PASS_INDEX, TABLE_PASS_INDEX
from vtf.render.bridge import find_blender, render_run, render_scene

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "spec" / "examples" / "scene_example.json"
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
