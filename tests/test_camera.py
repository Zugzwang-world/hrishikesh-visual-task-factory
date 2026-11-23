"""Camera-quality dial: presets, the lens warp, determinism, and applying it to a render folder.

No Blender: the render folder is the contract fixture in spec/examples/run_example.
"""

import shutil
from pathlib import Path

import numpy as np
import pytest

from vtf.camera import (
    PRESETS,
    apply_to_beauty,
    distort_points,
    is_clean,
    preset,
    undistort_points,
    warp_index,
    warp_nearest,
)
from vtf.camera.apply import apply_render_dir, dial_applied
from vtf.camera.dial import fill_scale, source_coordinates
from vtf.core.io import (
    read_depth,
    read_index_png,
    read_manifest,
    read_mask_png,
    read_rgb_png,
    read_scene,
    write_model,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "spec" / "examples" / "run_example" / "scenes" / "scene_0000"
K = np.array([[700.0, 0.0, 512.0], [0.0, 700.0, 384.0], [0.0, 0.0, 1.0]])


def test_presets() -> None:
    assert list(PRESETS) == ["clean", "mild", "harsh", "cctv"]
    assert is_clean(preset("clean"))
    for level in ("mild", "harsh", "cctv"):
        q = preset(level, seed=3)
        assert q.level == level and q.seed == 3 and not is_clean(q)
    assert preset("cctv").cctv
    with pytest.raises(ValueError):
        preset("fisheye")


@pytest.mark.parametrize("k1", [-0.18, -0.04, 0.1])
def test_distortion_round_trip_and_fill(k1) -> None:
    uv = np.random.default_rng(0).uniform([0, 0], [1024, 768], (500, 2))
    back = undistort_points(distort_points(uv, k1, K), k1, K)
    np.testing.assert_allclose(back, uv, atol=1e-6)
    _, _, valid = source_coordinates((768, 1024), k1, K)
    assert valid.all()  # the fill zoom leaves no black border
    corners = np.array([[0.0, 0.0], [1024.0, 768.0]])
    if k1 < 0:  # barrel: the frame corners map onto themselves
        np.testing.assert_allclose(distort_points(corners, k1, K), corners, atol=1e-9)
    assert (fill_scale(k1, K) > 1) == (k1 < 0)


def test_warp_moves_points_with_the_map() -> None:
    """A labelled disc lands where distort_points sends its centre, for every warped pass."""
    h, w = 768, 1024
    yy, xx = np.mgrid[0:h, 0:w] + 0.5
    centre = np.array([900.0, 120.0])
    index = (np.hypot(xx - centre[0], yy - centre[1]) < 20).astype(np.uint16) * 7
    q = preset("cctv")
    warped = warp_index(index, q, K)
    rows, cols = np.nonzero(warped == 7)
    moved = distort_points(centre, q, K)[0]
    assert abs(cols.mean() + 0.5 - moved[0]) < 1.0 and abs(rows.mean() + 0.5 - moved[1]) < 1.0
    assert np.linalg.norm(moved - centre) > 10  # the distortion is not a no-op here
    depth = np.where(index > 0, 1.5, 0.0).astype(np.float32)
    assert np.array_equal(warp_nearest(depth, q, K, 0.0) > 0, warped > 0)
    assert np.array_equal(warp_index(index, preset("clean"), K), index)


def test_apply_to_beauty_is_seeded() -> None:
    rgb = np.random.default_rng(1).integers(0, 256, (120, 160, 3), dtype=np.uint8)
    k = np.array([[150.0, 0, 80], [0, 150.0, 60], [0, 0, 1]])
    assert np.array_equal(apply_to_beauty(rgb, preset("clean"), k), rgb)
    for level in ("mild", "harsh", "cctv"):
        a = apply_to_beauty(rgb, preset(level, 5), k)
        assert a.shape == rgb.shape and a.dtype == np.uint8
        assert np.array_equal(a, apply_to_beauty(rgb, preset(level, 5), k))
        assert not np.array_equal(a, rgb)
    assert not np.array_equal(
        apply_to_beauty(rgb, preset("harsh", 5), k), apply_to_beauty(rgb, preset("harsh", 6), k)
    )


def test_apply_render_dir(tmp_path) -> None:
    out = tmp_path / "render"
    shutil.copytree(FIXTURE / "render", out)
    scene = read_scene(FIXTURE / "scene.json")
    cams = [c.model_copy(update={"quality": preset("harsh", 11)}) for c in scene.cameras]
    scene = scene.model_copy(update={"cameras": cams})
    write_model(tmp_path / "scene.json", scene)
    before = read_manifest(out / "render_manifest.json")
    assert not dial_applied(before, scene)
    cam = before.cameras[0]
    k = np.asarray(cam.K)
    q = cams[0].quality
    index0 = read_index_png(out / cam.index)
    depth0 = read_depth(out / cam.depth)
    beauty0 = read_rgb_png(out / cam.beauty)

    manifest, _ = apply_render_dir(out, scene)
    after = manifest.camera(cam.camera_id)
    assert after.beauty_raw == "front/beauty_raw.png" and after.quality["level"] == "harsh"
    assert dial_applied(read_manifest(out / "render_manifest.json"), scene)
    assert np.array_equal(read_rgb_png(out / after.beauty_raw), beauty0)
    assert np.array_equal(read_rgb_png(out / after.beauty), apply_to_beauty(beauty0, q, k))
    index1 = read_index_png(out / cam.index)
    assert np.array_equal(index1, warp_index(index0, q, k))
    assert np.array_equal(read_depth(out / cam.depth), warp_nearest(depth0, q, k, 0.0))
    for p, rel in cam.amodal.items():
        visible = index1 == int(p)
        assert not (visible & ~read_mask_png(out / rel)).any()  # still a subset after the warp

    again, _ = apply_render_dir(out, scene)  # idempotent: no double warp
    assert np.array_equal(read_index_png(out / cam.index), index1)
    assert again == manifest


def test_apply_render_dir_without_raw(tmp_path) -> None:
    """keep_raw False (scale runs): same dialled image, no beauty_raw.png left behind."""
    out = tmp_path / "render"
    shutil.copytree(FIXTURE / "render", out)
    scene = read_scene(FIXTURE / "scene.json")
    cams = [c.model_copy(update={"quality": preset("cctv", 2)}) for c in scene.cameras]
    scene = scene.model_copy(update={"cameras": cams})
    cam = read_manifest(out / "render_manifest.json").cameras[0]
    beauty0 = read_rgb_png(out / cam.beauty)
    manifest, _ = apply_render_dir(out, scene, keep_raw=False)
    after = manifest.camera(cam.camera_id)
    assert after.beauty_raw is None and after.quality["level"] == "cctv"
    assert not (out / "front" / "beauty_raw.png").exists()
    expected = apply_to_beauty(beauty0, cams[0].quality, np.asarray(cam.K))
    assert np.array_equal(read_rgb_png(out / after.beauty), expected)
    assert dial_applied(read_manifest(out / "render_manifest.json"), scene)
