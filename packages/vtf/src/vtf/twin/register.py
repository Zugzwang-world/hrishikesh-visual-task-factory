"""Gate G8: register a photo of a physical twin onto its layout and check the placement.

1. Markers. Detect the mat's four ArUco markers (OpenCV DICT_4X4_50, ids 0 to 3) on the full
   photo, at least MIN_MARKERS of them (`detect_markers`, `register_photo` fits the plain
   photo-to-mat homography).
2. Camera pose. solvePnP on the marker corners (the mat_spec geometry, on the table top at the
   scene's table height) with the photo's intrinsics: square pixels, centred principal point, no
   distortion, focal length from the photo's EXIF 35 mm-equivalent focal length when present,
   else the guide's nominal lens (26 mm on a 36 mm sensor, as the twin scenes use). IPPE gives the
   planar solutions, Levenberg-Marquardt refines the best. Accepted when the corner reprojection
   RMS is at most MAX_REPROJ_PX (at 1024 px width; scaled with the photo).
3. Placement. At the working width (1024 px) the photo is compared with the empty mat as the
   pose predicts it (docs/twins/mat_texture.png warped into the photo, lit by a smooth
   illumination field fitted on paper pixels away from the objects): pixels that differ in
   brightness or colour from the printed mat are "not mat". Each object of the layout's settled
   scene is projected into the photo (convex hull of its mesh), and its position is searched on
   the table plane (5 mm grid over +-6 cm, then 1 mm) for the shift whose silhouette best covers
   not-mat pixels and least covers bare mat, with the other objects' silhouettes masked out. The
   placement error is the length of that shift.
4. Verdict. Pass when the pose is accepted and every measurable object is found (at least
   MIN_COVERAGE of its silhouette is not-mat at the best shift) within PLACEMENT_TOL_M. Not
   measured, and so not enforced: an object mostly hidden behind or inside other objects in this
   view (less than MIN_MEASURABLE of its silhouette outside theirs, or under MIN_VISIBLE_PX), and
   a white object (LOW_CONTRAST_COLOURS), which on the white paper looks like a shadow. The
   overlay (`overlay`) draws every outline for a human glance.

The estimated camera becomes the photo's camera in the sibling scene (vtf.twin.real), so the
answers and the synthetic sibling come from exactly where the phone was.
"""

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from vtf.core.geometry import matrix_to_quat, round_vec
from vtf.core.projection import BLENDER_TO_CV
from vtf.core.scene import CameraQuality, CameraSpec, SceneSpec
from vtf.twin.mat import ARUCO_DICT, MatSpec, default_mat_spec

G8_VERSION = "0.1.0"
MIN_MARKERS = 3
UPSCALE_BELOW_PX = 2000
MAX_REPROJ_PX = 2.0  # marker-corner RMS at 1024 px width
WORK_WIDTH = 1024
NOMINAL_FOCAL_MM = 26.0
NOMINAL_SENSOR_MM = 36.0
FULL_FRAME_DIAGONAL_MM = math.hypot(36.0, 24.0)  # 35 mm-equivalent focal lengths use this
PLACEMENT_TOL_M = 0.02
SEARCH_M = 0.06
COARSE_STEP_M = 0.005
FINE_STEP_M = 0.001
MIN_COVERAGE = 0.5
MIN_MEASURABLE = 0.3
MIN_VISIBLE_PX = 150
MAT_INSET_M = 0.004  # ignore the outermost mat edge (paper edge, table edge)
KEEP_OUT_GROW_M = 0.003  # and the marker squares, grown a little
LUM_TOL = 0.18  # |photo - expected| / illumination
CHROMA_TOL = 0.045  # chromaticity distance from the paper's
SILHOUETTE_GROW_PX = 12  # paper samples for the illumination fit stay this far from objects
# A white object on the white mat differs from the paper only by shading, about as much as a
# shadow does, so the mat comparison cannot place it (digital twins: the tape roll and the spray
# bottle were missed or misplaced in 11 of 36 views). Their placement is shown on the overlay for
# a human check, not enforced.
LOW_CONTRAST_COLOURS = frozenset({"white"})

Matrix3 = list[list[float]]


# Markers and the plain homography (kept from the stub) -----------------------------------------


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = Field(description="Enough markers found and the fit residual is small.")
    marker_ids: list[int] = Field(description="Mat marker ids found in the photo, sorted.")
    image_size: tuple[int, int] = Field(description="Photo width, height in pixels.")
    photo_to_mat: Matrix3 | None = Field(
        default=None, description="3x3 homography: photo pixel (u, v, 1) -> mat metres (x, y, 1)."
    )
    mat_to_photo: Matrix3 | None = Field(default=None, description="Inverse of photo_to_mat.")
    rms_mm: float | None = Field(default=None, description="Marker-corner residual on the mat.")
    reason: str = ""


def _detector() -> cv2.aruco.ArucoDetector:
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(ARUCO_DICT), params)


def detect_markers(image: np.ndarray) -> dict[int, np.ndarray]:
    """Marker id -> (4, 2) float corners in photo pixels (OpenCV order: TL, TR, BR, BL).

    Images narrower than UPSCALE_BELOW_PX are searched at twice the size: the far markers of a
    1024 px render are about 20 px wide, and the detector finds them reliably only when doubled
    (36 of 36 digital-twin views give at least 3 markers, against 34 at the native size)."""
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    scale = 2 if gray.shape[1] < UPSCALE_BELOW_PX else 1
    if scale != 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    corners, ids, _ = _detector().detectMarkers(gray)
    if ids is None:
        return {}
    found: dict[int, np.ndarray] = {}
    for c, marker_id in zip(corners, ids.ravel(), strict=True):
        pts = c.reshape(4, 2).astype(np.float64)
        found.setdefault(int(marker_id), (pts + 0.5) / scale - 0.5)  # OpenCV pixel centres
    return found


def map_points(h: np.ndarray | Matrix3, points: np.ndarray) -> np.ndarray:
    """Apply a 3x3 homography to (N, 2) points."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, np.asarray(h, dtype=np.float64)).reshape(-1, 2)


def register_photo(
    image: np.ndarray,
    spec: MatSpec | None = None,
    min_markers: int = 3,
    max_rms_mm: float = 3.0,
) -> Registration:
    """Detect the mat markers in a photo (BGR or grey) and fit photo -> mat."""
    spec = spec or default_mat_spec()
    height, width = image.shape[:2]
    known = {m.id: np.asarray(m.corners_xy, dtype=np.float64) for m in spec.markers}
    found = {k: v for k, v in detect_markers(image).items() if k in known}
    ids = sorted(found)
    if len(ids) < max(min_markers, 1):
        return Registration(
            ok=False,
            marker_ids=ids,
            image_size=(width, height),
            reason=f"found {len(ids)} of {len(known)} mat markers, need {min_markers}",
        )
    photo_pts = np.concatenate([found[i] for i in ids])
    mat_pts = np.concatenate([known[i] for i in ids])
    h, _ = cv2.findHomography(photo_pts, mat_pts, method=0)
    if h is None:
        return Registration(
            ok=False, marker_ids=ids, image_size=(width, height), reason="homography fit failed"
        )
    residual = map_points(h, photo_pts) - mat_pts
    rms_mm = float(np.sqrt(np.mean(np.sum(residual**2, axis=1)))) * 1000
    ok = rms_mm <= max_rms_mm
    return Registration(
        ok=ok,
        marker_ids=ids,
        image_size=(width, height),
        photo_to_mat=h.tolist(),
        mat_to_photo=np.linalg.inv(h).tolist(),
        rms_mm=round(rms_mm, 4),
        reason="" if ok else f"corner residual {rms_mm:.2f} mm above {max_rms_mm} mm",
    )


# Camera pose ----------------------------------------------------------------------------------


def photo_intrinsics(width: int, height: int, focal_35mm: float | None = None) -> np.ndarray:
    """K for a photo: from its 35 mm-equivalent focal length (diagonal convention) when known,
    else the nominal 26 mm on a 36 mm-wide sensor (horizontal, like the twin scene cameras)."""
    if focal_35mm:
        f = focal_35mm * math.hypot(width, height) / FULL_FRAME_DIAGONAL_MM
    else:
        f = NOMINAL_FOCAL_MM / NOMINAL_SENSOR_MM * width
    return np.array([[f, 0.0, width / 2.0], [0.0, f, height / 2.0], [0.0, 0.0, 1.0]])


def exif_focal_35mm(path: Path) -> float | None:
    """FocalLengthIn35mmFilm from a JPEG's EXIF (phones write it), else None."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            exif = im.getexif()
            value = exif.get_ifd(0x8769).get(0xA405) or exif.get(0xA405)
        return float(value) if value else None
    except Exception:
        return None


class PoseEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    marker_ids: list[int]
    image_size: tuple[int, int]
    focal_px: float
    focal_source: str = Field(description="exif, nominal or given.")
    reprojection_rms_px: float | None = Field(
        default=None, description="Marker-corner RMS, scaled to a 1024 px wide image."
    )
    rvec: list[float] | None = None
    tvec: list[float] | None = None
    camera_position: list[float] | None = Field(default=None, description="World metres.")
    tilt_down_deg: float | None = None
    reason: str = ""


def estimate_pose(
    image: np.ndarray,
    k: np.ndarray,
    table_height: float,
    spec: MatSpec | None = None,
    focal_source: str = "nominal",
) -> PoseEstimate:
    """Camera pose from the marker corners (world frame: mat x, y on the table top)."""
    spec = spec or default_mat_spec()
    height, width = image.shape[:2]
    known = {m.id: np.asarray(m.corners_xy, dtype=np.float64) for m in spec.markers}
    found = {i: c for i, c in detect_markers(image).items() if i in known}
    ids = sorted(found)
    base = dict(
        marker_ids=ids, image_size=(width, height), focal_px=float(k[0, 0]),
        focal_source=focal_source,
    )  # fmt: skip
    if len(ids) < MIN_MARKERS:
        return PoseEstimate(
            ok=False, reason=f"found {len(ids)} of 4 mat markers, need {MIN_MARKERS}", **base
        )
    img_pts = np.concatenate([found[i] for i in ids])
    obj_xy = np.concatenate([known[i] for i in ids])
    obj_pts = np.column_stack([obj_xy, np.full(len(obj_xy), table_height)])
    dist = np.zeros(5)
    # IPPE wants the plane at z = 0: solve in the mat frame, then lift by the table height.
    plane_pts = np.column_stack([obj_xy, np.zeros(len(obj_xy))])
    n, rvecs, tvecs, errs = cv2.solvePnPGeneric(
        plane_pts, img_pts, k, dist, flags=cv2.SOLVEPNP_IPPE
    )
    if not n:
        return PoseEstimate(ok=False, reason="solvePnP found no pose", **base)
    best = int(np.argmin(np.asarray(errs).ravel()))
    rvec, tvec = cv2.solvePnPRefineLM(plane_pts, img_pts, k, dist, rvecs[best], tvecs[best])
    rot, _ = cv2.Rodrigues(rvec)
    tvec = tvec.reshape(3) - rot @ np.array([0.0, 0.0, table_height])  # world z = mat z + h
    proj, _ = cv2.projectPoints(obj_pts, rvec, tvec, k, dist)
    rms = float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - img_pts) ** 2, axis=1))))
    rms_1024 = rms * WORK_WIDTH / width
    centre = -rot.T @ tvec
    view = rot.T @ np.array([0.0, 0.0, 1.0])
    tilt = math.degrees(math.asin(max(-1.0, min(1.0, -view[2]))))
    ok = rms_1024 <= MAX_REPROJ_PX and centre[2] > table_height
    reason = ""
    if rms_1024 > MAX_REPROJ_PX:
        reason = (
            f"marker reprojection {rms_1024:.2f} px (at 1024 px) above {MAX_REPROJ_PX} px: "
            "wrong focal length or a curled mat"
        )
    elif centre[2] <= table_height:
        reason = "camera below the table"
    return PoseEstimate(
        ok=ok,
        reprojection_rms_px=round(rms_1024, 4),
        rvec=[float(v) for v in rvec.ravel()],
        tvec=[float(v) for v in tvec.ravel()],
        camera_position=[round(float(v), 6) for v in centre],
        tilt_down_deg=round(tilt, 3),
        reason=reason,
        **base,
    )


def pose_camera(
    pose: PoseEstimate, camera_id: str, rig: str, resolution: tuple[int, int], seed: int = 0
) -> CameraSpec:
    """The estimated pose as a scene camera at `resolution` (same field of view as the photo)."""
    rot, _ = cv2.Rodrigues(np.asarray(pose.rvec, dtype=np.float64))
    c2w_cv = rot.T
    rotation = matrix_to_quat(c2w_cv @ BLENDER_TO_CV)
    width = pose.image_size[0]
    focal_mm = pose.focal_px / width * NOMINAL_SENSOR_MM
    return CameraSpec(
        id=camera_id,
        rig=rig,  # type: ignore[arg-type]
        pose={"position": round_vec(pose.camera_position), "rotation": round_vec(rotation)},
        focal_mm=round(focal_mm, 6),
        sensor_width_mm=NOMINAL_SENSOR_MM,
        resolution=resolution,
        quality=CameraQuality(seed=seed),
    )


def camera_matrices(cam: CameraSpec) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """K, R (world -> OpenCV camera) and t for a scene camera."""
    from vtf.core.projection import intrinsics, world_to_camera

    w2c = world_to_camera(cam)
    return intrinsics(cam), w2c[:3, :3], w2c[:3, 3]


def pose_error(est: CameraSpec, truth: CameraSpec) -> dict:
    """Position (mm) and rotation (degrees) difference between two cameras."""
    _, r1, _ = camera_matrices(est)
    _, r2, _ = camera_matrices(truth)
    cos = (np.trace(r1 @ r2.T) - 1) / 2
    return {
        "position_mm": round(
            float(np.linalg.norm(np.subtract(est.pose.position, truth.pose.position))) * 1000, 2
        ),
        "rotation_deg": round(math.degrees(math.acos(max(-1.0, min(1.0, cos)))), 3),
        "focal_mm": round(est.focal_mm - truth.focal_mm, 3),
    }


# Placement ------------------------------------------------------------------------------------


@lru_cache(maxsize=4)
def _texture(path: str) -> tuple[np.ndarray, np.ndarray]:
    """Mat texture as float grey albedo (0..1) and an 'inked in colour' mask."""
    bgr = cv2.imread(path, cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(f"mat texture not found: {path} (uv run vtf twin texture)")
    rgb = bgr[..., ::-1].astype(np.float32) / 255.0
    grey = rgb.mean(axis=2)
    chroma = np.abs(rgb - grey[..., None]).max(axis=2)
    return grey, (chroma > 0.06).astype(np.float32)


def _hull_vertices(obj) -> np.ndarray:
    from vtf.core.assets import object_mesh

    mesh = object_mesh(obj)
    try:
        return np.asarray(mesh.convex_hull.vertices, dtype=np.float64)
    except Exception:
        return np.asarray(mesh.vertices, dtype=np.float64)


def _project(k, rot, t, pts: np.ndarray) -> np.ndarray:
    pc = pts @ rot.T + t
    z = np.maximum(pc[:, 2], 1e-6)
    return np.column_stack([k[0, 0] * pc[:, 0] / z + k[0, 2], k[1, 1] * pc[:, 1] / z + k[1, 2]])


def _silhouette(k, rot, t, hull3d: np.ndarray, size: tuple[int, int], shift=(0.0, 0.0)):
    """Filled convex hull of the projected (shifted) hull vertices: mask and bounding box."""
    pts = hull3d + np.array([shift[0], shift[1], 0.0])
    uv = _project(k, rot, t, pts)
    poly = cv2.convexHull(np.round(uv * 4).astype(np.int32))  # 2 fractional bits
    width, height = size
    mask = np.zeros((height, width), np.uint8)
    cv2.fillConvexPoly(mask, poly, 1, lineType=cv2.LINE_8, shift=2)
    return mask.astype(bool)


@dataclass
class PlacementContext:
    k: np.ndarray
    rot: np.ndarray
    t: np.ndarray
    size: tuple[int, int]
    not_mat: np.ndarray  # bool, photo pixels that are not the printed mat
    valid: np.ndarray  # bool, photo pixels where the mat should be (the comparison region)


def mat_homography(k: np.ndarray, rot: np.ndarray, t: np.ndarray, table_h: float) -> np.ndarray:
    """3x3: mat metres (x, y, 1) on the table top -> photo pixels."""
    h = np.column_stack([rot[:, 0], rot[:, 1], rot[:, 2] * table_h + t])
    return k @ h


def not_mat_mask(
    photo_bgr: np.ndarray,
    k: np.ndarray,
    rot: np.ndarray,
    t: np.ndarray,
    table_h: float,
    expected_objects: np.ndarray,
    texture_path: Path | None = None,
    spec: MatSpec | None = None,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """(not_mat, valid, stats): photo pixels that differ from the printed mat, and the region
    where that comparison is made (the mat minus its edge and the marker squares)."""
    from vtf.twin.digital import default_texture_path, texture_to_mat

    spec = spec or default_mat_spec()
    height, width = photo_bgr.shape[:2]
    albedo_tex, colour_tex = _texture(str(texture_path or default_texture_path()))
    mat_to_px = mat_homography(k, rot, t, table_h)
    tex_to_px = mat_to_px @ texture_to_mat()
    warp = dict(dsize=(width, height), flags=cv2.INTER_AREA, borderValue=0)
    albedo = cv2.warpPerspective(albedo_tex, tex_to_px, **warp)
    inked = cv2.warpPerspective(colour_tex, tex_to_px, **warp) > 0.05
    # Comparison region: the mat, inset, minus the marker squares.
    hx, hy = (v / 2 - MAT_INSET_M for v in spec.size_xy)
    region = np.zeros((height, width), np.uint8)
    corners = map_points(mat_to_px, np.array([[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy]]))
    cv2.fillConvexPoly(region, np.round(corners * 4).astype(np.int32), 1, shift=2)
    for r in spec.keep_out:
        g = KEEP_OUT_GROW_M
        box = np.array([[r.x_min - g, r.y_min - g], [r.x_max + g, r.y_min - g],
                        [r.x_max + g, r.y_max + g], [r.x_min - g, r.y_max + g]])  # fmt: skip
        cv2.fillConvexPoly(region, np.round(map_points(mat_to_px, box) * 4).astype(np.int32), 0,
                           shift=2)  # fmt: skip
    valid = region.astype(bool)

    rgb = photo_bgr[..., ::-1].astype(np.float32) / 255.0
    lum = cv2.GaussianBlur(rgb.mean(axis=2), (0, 0), 1.0)
    albedo_b = cv2.GaussianBlur(albedo, (0, 0), 1.0)
    grow = cv2.dilate(
        expected_objects.astype(np.uint8),
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * SILHOUETTE_GROW_PX + 1,) * 2),
    ).astype(bool)
    paper = valid & ~grow & (albedo_b > 0.9) & ~inked
    if paper.sum() < 500:
        return np.zeros_like(valid), valid, {"reason": "too little bare mat to compare"}
    sigma = 0.04 * width
    w = paper.astype(np.float32)
    num = cv2.GaussianBlur(lum * w, (0, 0), sigma)
    den = cv2.GaussianBlur(w, (0, 0), sigma)
    illum = num / np.maximum(den, 1e-6)
    sparse = den < 0.05
    if sparse.any():  # far from any paper sample: a wider fit
        num2 = cv2.GaussianBlur(lum * w, (0, 0), 4 * sigma)
        den2 = cv2.GaussianBlur(w, (0, 0), 4 * sigma)
        illum = np.where(sparse, num2 / np.maximum(den2, 1e-6), illum)
    illum = np.maximum(illum, 1e-3)
    lum_diff = np.abs(lum - illum * albedo_b) / illum
    s = rgb.sum(axis=2, keepdims=True)
    chrom = rgb / np.maximum(s, 1e-6)
    paper_chrom = np.median(chrom[paper], axis=0)
    chroma_dist = np.linalg.norm(chrom - paper_chrom, axis=2)
    reliable = ~cv2.dilate(inked.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    bright = lum > 0.08
    not_mat = (lum_diff > LUM_TOL) | ((chroma_dist > CHROMA_TOL) & reliable & bright)
    not_mat &= valid
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    not_mat = cv2.morphologyEx(not_mat.astype(np.uint8), cv2.MORPH_OPEN, kernel)
    not_mat = cv2.morphologyEx(not_mat, cv2.MORPH_CLOSE, kernel).astype(bool) & valid
    false_rate = float(not_mat[paper].mean())
    return not_mat, valid, {"paper_pixels": int(paper.sum()), "paper_flagged": round(false_rate, 4)}


class ObjectPlacement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: str
    name: str
    measured: bool
    error_mm: float | None = None
    shift_mm: list[float] | None = None
    coverage: float | None = Field(
        default=None, description="Share of its silhouette that is not mat, at the best shift."
    )
    coverage_at_layout: float | None = None
    measurable_fraction: float | None = None
    ok: bool
    note: str = ""


def _score_shift(ctx: PlacementContext, hull, shift, exclude: np.ndarray) -> tuple[float, float]:
    sil = _silhouette(ctx.k, ctx.rot, ctx.t, hull, ctx.size, shift) & ctx.valid & ~exclude
    n = int(sil.sum())
    if n == 0:
        return -1e9, 0.0
    hit = int(ctx.not_mat[sil].sum())
    return float(2 * hit - n), hit / n


def measure_object(
    ctx: PlacementContext, hull: np.ndarray, exclude: np.ndarray, nominal_area: int
) -> tuple[tuple[float, float], float, float]:
    """Best table-plane shift (metres), its coverage, and the coverage at zero shift."""
    best, best_cov, best_shift = -1e18, 0.0, (0.0, 0.0)
    steps = np.arange(-SEARCH_M, SEARCH_M + 1e-9, COARSE_STEP_M)
    for dx in steps:
        for dy in steps:
            score, cov = _score_shift(ctx, hull, (dx, dy), exclude)
            # Prefer the smaller shift on ties (a plateau, e.g. a shadow beside the object).
            if score > best + 1e-9 or (abs(score - best) <= 1e-9 and math.hypot(dx, dy) <
                                       math.hypot(*best_shift)):  # fmt: skip
                best, best_cov, best_shift = score, cov, (float(dx), float(dy))
    fine = np.arange(-COARSE_STEP_M, COARSE_STEP_M + 1e-9, FINE_STEP_M)
    cx, cy = best_shift
    for dx in fine:
        for dy in fine:
            shift = (cx + dx, cy + dy)
            score, cov = _score_shift(ctx, hull, shift, exclude)
            if score > best + 1e-9:
                best, best_cov, best_shift = score, cov, (float(shift[0]), float(shift[1]))
    _, cov0 = _score_shift(ctx, hull, (0.0, 0.0), exclude)
    return best_shift, best_cov, cov0


def check_placement(
    photo_bgr: np.ndarray,
    cam: CameraSpec,
    settled: SceneSpec,
    texture_path: Path | None = None,
    tolerance_m: float = PLACEMENT_TOL_M,
) -> tuple[list[ObjectPlacement], dict]:
    """Per-object placement errors of a photo (already at cam.resolution) against a layout."""
    from vtf.core.raycast import raycast

    width, height = cam.resolution
    assert photo_bgr.shape[:2] == (height, width), "resize the photo to the camera first"
    k, rot, t = camera_matrices(cam)
    hulls = {o.id: _hull_vertices(o) for o in settled.objects}
    sils = {oid: _silhouette(k, rot, t, h, (width, height)) for oid, h in hulls.items()}
    union = np.zeros((height, width), bool)
    for m in sils.values():
        union |= m
    not_mat, valid, stats = not_mat_mask(
        photo_bgr, k, rot, t, settled.table.height, union, texture_path
    )
    ctx = PlacementContext(k, rot, t, (width, height), not_mat, valid)
    probe = settled.model_copy(update={"cameras": [cam]})
    visible = np.bincount(raycast(probe, cam.id).index.ravel(), minlength=1001)
    results = []
    for obj in settled.objects:
        others = np.zeros((height, width), bool)
        for oid, m in sils.items():
            if oid != obj.id:
                others |= m
        own = sils[obj.id] & valid
        free = own & ~others
        frac = float(free.sum() / own.sum()) if own.sum() else 0.0
        vis_px = int(visible[obj.pass_index]) if obj.pass_index < len(visible) else 0
        if (obj.color_name or "") in LOW_CONTRAST_COLOURS:
            results.append(
                ObjectPlacement(
                    object_id=obj.id, name=obj.name, measured=False, ok=True,
                    measurable_fraction=round(frac, 3),
                    note="white on the white mat: not checked automatically (see the overlay)",
                )  # fmt: skip
            )
            continue
        if frac < MIN_MEASURABLE or vis_px < MIN_VISIBLE_PX or free.sum() < MIN_VISIBLE_PX:
            results.append(
                ObjectPlacement(
                    object_id=obj.id, name=obj.name, measured=False, ok=True,
                    measurable_fraction=round(frac, 3),
                    note="mostly hidden behind or inside other objects in this view",
                )  # fmt: skip
            )
            continue
        shift, cov, cov0 = measure_object(ctx, hulls[obj.id], others, int(own.sum()))
        err = math.hypot(*shift)
        found = cov >= MIN_COVERAGE
        ok = found and err <= tolerance_m
        note = "" if found else "not found: its silhouette is mostly bare mat everywhere nearby"
        if found and not ok:
            note = f"{err * 100:.1f} cm from its layout position (tolerance {tolerance_m * 100:g})"
        results.append(
            ObjectPlacement(
                object_id=obj.id, name=obj.name, measured=True,
                error_mm=round(err * 1000, 1),
                shift_mm=[round(shift[0] * 1000, 1), round(shift[1] * 1000, 1)],
                coverage=round(cov, 3), coverage_at_layout=round(cov0, 3),
                measurable_fraction=round(frac, 3), ok=ok, note=note,
            )  # fmt: skip
        )
    return results, stats


# The gate -------------------------------------------------------------------------------------


class G8Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    g8_version: str = G8_VERSION
    passed: bool
    reason: str = ""
    photo: str
    photo_sha256: str
    layout_scene_id: str
    camera_id: str
    pose: PoseEstimate
    camera: CameraSpec | None = None
    objects: list[ObjectPlacement] = Field(default_factory=list)
    mat_comparison: dict = Field(default_factory=dict)
    tolerances: dict = Field(default_factory=dict)
    pose_error: dict | None = Field(
        default=None, description="Against a known camera (digital twins only)."
    )


def tolerances(tolerance_m: float = PLACEMENT_TOL_M) -> dict:
    return {
        "min_markers": MIN_MARKERS,
        "max_reprojection_px_at_1024": MAX_REPROJ_PX,
        "placement_m": tolerance_m,
        "min_coverage": MIN_COVERAGE,
        "search_m": SEARCH_M,
        "min_measurable_fraction": MIN_MEASURABLE,
        "not_checked_colours": sorted(LOW_CONTRAST_COLOURS),
    }


def expected_silhouettes(cam: CameraSpec, settled: SceneSpec) -> dict[str, np.ndarray]:
    """Object id -> bool mask of its projected convex hull at its settled pose."""
    k, rot, t = camera_matrices(cam)
    size = cam.resolution
    return {o.id: _silhouette(k, rot, t, _hull_vertices(o), size) for o in settled.objects}


def layout_match(work: np.ndarray, cam: CameraSpec, settled: SceneSpec) -> float:
    """How well a photo fits a layout: IoU of the photo's not-mat pixels with the union of the
    layout's object silhouettes (coloured objects only), inside the comparison region."""
    k, rot, t = camera_matrices(cam)
    sils = expected_silhouettes(cam, settled)
    union = np.zeros(work.shape[:2], bool)
    coloured = np.zeros(work.shape[:2], bool)
    for o in settled.objects:
        union |= sils[o.id]
        if (o.color_name or "") not in LOW_CONTRAST_COLOURS:
            coloured |= sils[o.id]
    not_mat, valid, _ = not_mat_mask(work, k, rot, t, settled.table.height, union)
    white = union & ~coloured  # white objects neither count for nor against a layout
    a, b = not_mat & valid & ~white, coloured & valid
    inter, uni = int((a & b).sum()), int((a | b).sum())
    return inter / uni if uni else 0.0


def overlay(work: np.ndarray, result: G8Result, settled: SceneSpec, title: str) -> np.ndarray:
    """The photo with every object's expected outline: green placed, red rejected (with its
    measured position dashed), yellow not checked; not-mat pixels tinted."""
    cam = result.camera
    assert cam is not None
    k, rot, t = camera_matrices(cam)
    sils = expected_silhouettes(cam, settled)
    union = np.zeros(work.shape[:2], bool)
    for m in sils.values():
        union |= m
    not_mat, valid, _ = not_mat_mask(work, k, rot, t, settled.table.height, union)
    vis = work.copy()
    vis[not_mat] = (0.6 * vis[not_mat] + [0, 0, 100]).astype(np.uint8)
    by_id = {p.object_id: p for p in result.objects}
    for obj in settled.objects:
        p = by_id.get(obj.id)
        colour = (0, 200, 255) if p is None or not p.measured else (
            (0, 190, 0) if p.ok else (0, 0, 255))  # fmt: skip
        cnts, _ = cv2.findContours(sils[obj.id].astype(np.uint8), cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)  # fmt: skip
        cv2.drawContours(vis, cnts, -1, colour, 2)
        if p is not None and p.measured and not p.ok and p.shift_mm:
            moved = _silhouette(k, rot, t, _hull_vertices(obj), cam.resolution,
                                (p.shift_mm[0] / 1000, p.shift_mm[1] / 1000))  # fmt: skip
            cnts2, _ = cv2.findContours(moved.astype(np.uint8), cv2.RETR_EXTERNAL,
                                        cv2.CHAIN_APPROX_SIMPLE)  # fmt: skip
            cv2.drawContours(vis, cnts2, -1, (0, 0, 255), 1)
        if cnts:
            x, y, _, _ = cv2.boundingRect(np.concatenate(cnts))
            cv2.putText(vis, obj.name, (x, max(12, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                        colour, 1, cv2.LINE_AA)  # fmt: skip
    verdict = "G8 PASS" if result.passed else "G8 REJECT"
    cv2.rectangle(vis, (0, 0), (vis.shape[1], 26), (255, 255, 255), -1)
    cv2.putText(vis, f"{title}  {verdict}", (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (0, 140, 0) if result.passed else (0, 0, 220), 1, cv2.LINE_AA)  # fmt: skip
    return vis


def working_size(width: int, height: int) -> tuple[int, int]:
    return WORK_WIDTH, int(round(height * WORK_WIDTH / width))


def load_photo(path: Path) -> np.ndarray:
    """BGR photo with its EXIF orientation applied."""
    from PIL import Image, ImageOps

    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        return np.asarray(im)[..., ::-1].copy()


def photo_pose(
    photo_path: Path, table_height: float, focal_35mm: float | None = None
) -> tuple[np.ndarray, PoseEstimate]:
    """Load a photo and estimate the camera pose from its markers."""
    photo_path = Path(photo_path)
    image = load_photo(photo_path)
    height, width = image.shape[:2]
    if focal_35mm:
        source = "given"
    else:
        focal_35mm = exif_focal_35mm(photo_path)
        source = "exif" if focal_35mm else "nominal"
    k = photo_intrinsics(width, height, focal_35mm)
    return image, estimate_pose(image, k, table_height, focal_source=source)


def working_photo(image: np.ndarray, pose: PoseEstimate, camera_id: str, rig: str):
    """The photo at the working size and the matching scene camera."""
    height, width = image.shape[:2]
    size = working_size(width, height)
    cam = pose_camera(pose, camera_id, rig, size)
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA), cam


def run_g8(
    photo_path: Path,
    settled: SceneSpec,
    camera_id: str,
    rig: str,
    focal_35mm: float | None = None,
    truth: CameraSpec | None = None,
    texture_path: Path | None = None,
    tolerance_m: float = PLACEMENT_TOL_M,
    loaded: tuple[np.ndarray, PoseEstimate] | None = None,
) -> tuple[G8Result, np.ndarray | None]:
    """Gate G8 for one photo of one layout. Returns the result and the photo resized to the
    working size (the twin task's image), or None when the pose failed. `loaded` reuses a
    photo_pose result."""
    from vtf.core.hashing import sha256_file

    photo_path = Path(photo_path)
    image, pose = loaded or photo_pose(photo_path, settled.table.height, focal_35mm)
    base = dict(
        photo=photo_path.name, photo_sha256=sha256_file(photo_path),
        layout_scene_id=settled.meta.scene_id, camera_id=camera_id, pose=pose,
        tolerances=tolerances(tolerance_m),
    )  # fmt: skip
    if not pose.ok:
        return G8Result(passed=False, reason=f"pose: {pose.reason}", **base), None
    work, cam = working_photo(image, pose, camera_id, rig)
    placements, stats = check_placement(work, cam, settled, texture_path, tolerance_m)
    bad = [p for p in placements if not p.ok]
    reason = "; ".join(f"{p.name}: {p.note}" for p in bad)
    if "reason" in stats:
        bad, reason = [None], stats["reason"]
    result = G8Result(
        passed=not bad, reason=reason, camera=cam, objects=placements, mat_comparison=stats,
        pose_error=pose_error(cam, truth) if truth is not None else None, **base,
    )  # fmt: skip
    return result, work
