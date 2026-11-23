"""Physical twins: placement mat geometry and PDFs, ArUco registration (G8 stub), shopping list,
the twin recipe and the layout sheets."""

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from vtf.twin.mat import (
    A3_FILE,
    A4_FILE,
    SPEC_FILE,
    MatSpec,
    default_mat_spec,
    make_mat,
    marker_cells,
    rasterize_mat,
)
from vtf.twin.register import detect_markers, map_points, register_photo

REPO = Path(__file__).resolve().parents[1]


def _pdf_pages(path: Path) -> int:
    data = path.read_bytes()
    return data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") + data.count(b"/Type /Page>")


# Mat -------------------------------------------------------------------------------------------


def test_mat_spec_geometry() -> None:
    spec = default_mat_spec()
    assert spec.aruco_dictionary == "DICT_4X4_50"
    assert [m.id for m in spec.markers] == [0, 1, 2, 3]
    hx, hy = spec.size_xy[0] / 2, spec.size_xy[1] / 2
    for m, ko in zip(spec.markers, spec.keep_out, strict=True):
        xs, ys = zip(*m.corners_xy, strict=True)
        assert max(xs) - min(xs) == pytest.approx(spec.marker_size_m)
        assert max(ys) - min(ys) == pytest.approx(spec.marker_size_m)
        # OpenCV corner order seen from above (back edge up): TL, TR, BR, BL.
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = m.corners_xy
        assert x0 < x1 and y0 == y1 and y2 < y1 and x3 == x0
        assert -hx <= ko.x_min and ko.x_max <= hx and -hy <= ko.y_min and ko.y_max <= hy
        assert not spec.placement_area.intersects(ko)
    assert spec.marker_0_to_2_m == pytest.approx(
        float(np.hypot(*np.subtract(spec.markers[0].center_xy, spec.markers[2].center_xy)))
    )
    # mat_spec.json round-trips.
    assert MatSpec.model_validate_json(spec.model_dump_json()) == spec


def test_marker_cells_have_black_border() -> None:
    for marker_id in range(4):
        cells = marker_cells(marker_id)
        assert cells.shape == (6, 6)
        assert cells[0].all() and cells[-1].all() and cells[:, 0].all() and cells[:, -1].all()
        assert not cells[1:5, 1:5].all()


def test_make_mat_writes_small_deterministic_pdfs(tmp_path: Path) -> None:
    paths = make_mat(tmp_path / "a")
    make_mat(tmp_path / "b")
    assert [p.name for p in paths] == [A3_FILE, A4_FILE, SPEC_FILE]
    for p in paths:
        assert p.stat().st_size < 2_000_000
        assert p.read_bytes() == (tmp_path / "b" / p.name).read_bytes()
    assert paths[0].read_bytes().startswith(b"%PDF")
    assert _pdf_pages(paths[0]) == 1
    assert _pdf_pages(paths[1]) == 4
    spec = MatSpec.model_validate(json.loads(paths[2].read_text()))
    assert spec == default_mat_spec()


def test_tiles_cover_the_mat_with_overlap() -> None:
    spec = default_mat_spec()
    tiles = spec.prints[1].tiles
    assert len(tiles) == 4
    hx, hy = spec.size_xy[0] / 2, spec.size_xy[1] / 2
    rng = np.random.default_rng(0)
    for x, y in rng.uniform((-hx, -hy), (hx, hy), size=(500, 2)):
        assert any(t.window.contains(x, y) for t in tiles)
    for t in tiles:  # each window fits on A4 landscape with room for the page furniture
        assert (t.window.x_max - t.window.x_min) < 0.27
        assert (t.window.y_max - t.window.y_min) < 0.17


# Registration (G8 stub) ------------------------------------------------------------------------


def _synthetic_photo(occlude: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Rasterise the mat, warp it as a phone at the front would see it, add noise.

    Returns the photo and the true 3x3 map from mat metres to photo pixels.
    """
    spec = default_mat_spec()
    mat_img, world_to_px = rasterize_mat(spec, px_per_mm=2.0)
    h, w = mat_img.shape
    src = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
    dst = np.float32([[260, 140], [1020, 150], [1210, 830], [80, 810]])  # far edge narrower
    raster_to_photo = cv2.getPerspectiveTransform(src, dst)
    photo = cv2.warpPerspective(
        mat_img, raster_to_photo, (1280, 960), flags=cv2.INTER_AREA, borderValue=110
    )
    rng = np.random.default_rng(7)
    photo = np.clip(photo + rng.normal(0, 3, photo.shape), 0, 255).astype(np.uint8)
    mat_to_photo = raster_to_photo @ world_to_px
    if occlude is not None:
        centre = spec.markers[occlude].center_xy
        u, v = map_points(mat_to_photo, np.array([centre]))[0]
        cv2.circle(photo, (int(u), int(v)), 45, 60, -1)  # an object standing on the marker
    return photo, mat_to_photo


def test_rasterised_mat_markers_detected_at_known_corners() -> None:
    spec = default_mat_spec()
    img, world_to_px = rasterize_mat(spec, px_per_mm=4.0)
    found = detect_markers(img)
    assert sorted(found) == [0, 1, 2, 3]
    for m in spec.markers:
        expected = map_points(world_to_px, np.array(m.corners_xy))
        assert np.abs(found[m.id] - expected).max() < 1.0  # pixels (0.25 mm)


def test_register_synthetic_photo_recovers_homography() -> None:
    photo, mat_to_photo = _synthetic_photo()
    reg = register_photo(photo)
    assert reg.ok, reg.reason
    assert reg.marker_ids == [0, 1, 2, 3]
    assert reg.image_size == (1280, 960)
    assert reg.rms_mm is not None and reg.rms_mm < 1.0
    # Grid points across the whole mat map back to within a millimetre.
    xs, ys = np.meshgrid(np.linspace(-0.19, 0.19, 9), np.linspace(-0.13, 0.13, 7))
    mat_pts = np.column_stack([xs.ravel(), ys.ravel()])
    photo_pts = map_points(mat_to_photo, mat_pts)
    err_mm = np.linalg.norm(map_points(reg.photo_to_mat, photo_pts) - mat_pts, axis=1) * 1000
    assert err_mm.max() < 1.0
    # The inverse projects the layout into the photo to within a pixel or so.
    err_px = np.linalg.norm(map_points(reg.mat_to_photo, mat_pts) - photo_pts, axis=1)
    assert err_px.max() < 1.5


def test_register_with_one_marker_covered() -> None:
    photo, mat_to_photo = _synthetic_photo(occlude=2)
    reg = register_photo(photo)
    assert reg.ok and reg.marker_ids == [0, 1, 3]
    pts = np.array([[0.0, 0.0], [0.15, -0.09], [-0.15, 0.09]])
    back = map_points(reg.photo_to_mat, map_points(mat_to_photo, pts))
    assert np.abs(back - pts).max() < 0.0015
    strict = register_photo(photo, min_markers=4)
    assert not strict.ok and "3 of 4" in strict.reason and strict.photo_to_mat is None


def test_register_blank_photo_fails_cleanly() -> None:
    reg = register_photo(np.full((480, 640, 3), 200, np.uint8))
    assert not reg.ok and reg.marker_ids == [] and reg.photo_to_mat is None
