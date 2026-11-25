"""Digital twins of the physical-twin layouts: the same scene files with the printed mat on the
table, no gripper, and the photo-session camera spots.

`vtf twin digital out/twins --selection docs/twins/selection.json --out out/twins_digital` writes
one derived scene per selected layout into <out>/scenes/<scene_id>/scene.json (objects, lighting
and seed byte-for-byte from the layout's scene.json; only the table material, the gripper and the
cameras change), plus a `misplaced` run with one object of one layout shifted, the negative
control for gate G8. Render and validate them like any run.

The three photo spots (PHOTO_SPOTS) are the cameras the photo-session guide tells the founder to
use. front and overhead are the recipe's fixed spots (recipes/physical_twin.yaml); side is an
oblique from the front-right corner. A real photo is never assumed to be exactly at its spot: G8
estimates the phone's pose from the markers and the photo's sibling is rendered from there
(vtf.twin.real).
"""

import json
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from vtf.core.geometry import look_at_camera_quat, round_vec
from vtf.core.io import read_scene, write_model
from vtf.core.layout import RunLayout
from vtf.core.rng import derive_seed
from vtf.core.scene import CameraQuality, CameraSpec, SceneSpec
from vtf.twin.mat import GRID_MM, default_mat_spec

TWIN_MAT_MATERIAL = "twin_mat"
TEXTURE_FILE = "mat_texture.png"
TEXTURE_PX_PER_MM = 4
RENDER_PX_PER_MM = 8  # rasterise the PDF at 8 px/mm, then area-average down (anti-aliasing)
MISPLACED_RUN = "misplaced"
MISPLACE_SHIFT_M = 0.04
MISPLACE_MIN_GAP_M = 0.015
TWIN_SETTLE_FRAMES = 240  # 10 s at 24 fps
RESOLUTION = (1024, 768)
SENSOR_WIDTH_MM = 36.0
FOCAL_MM = 26.0  # about a phone's 1x camera on a 36 mm sensor (recipes/physical_twin.yaml)


@dataclass(frozen=True)
class PhotoSpot:
    """A phone position for the photo session, aimed at the mat centre."""

    id: str
    rig: str
    distance_m: float  # lens to mat centre
    elevation_deg: float  # 90 = straight down
    azimuth_deg: float  # 0 = in front of the mat (-y), positive turns toward +x (right)
    how: str  # one line for the guide

    def eye(self, table_height: float) -> np.ndarray:
        el, az = math.radians(self.elevation_deg), math.radians(self.azimuth_deg)
        horizontal = self.distance_m * math.cos(el)
        return np.array(
            [
                horizontal * math.sin(az),
                -horizontal * math.cos(az),
                table_height + self.distance_m * math.sin(el),
            ]
        )

    def height_cm(self) -> float:
        return self.distance_m * math.sin(math.radians(self.elevation_deg)) * 100

    def ground_cm(self) -> tuple[float, float]:
        """Lens position over the table plane, cm from the mat centre."""
        eye = self.eye(0.0)
        return eye[0] * 100, eye[1] * 100


PHOTO_SPOTS: tuple[PhotoSpot, ...] = (
    PhotoSpot("front", "front", 0.65, 50.0, 0.0, "in front of the mat, looking down at it"),
    PhotoSpot("overhead", "overhead", 0.60, 90.0, 0.0, "straight above the mat centre"),
    PhotoSpot("side", "front", 0.60, 45.0, 45.0, "at the front-right corner, looking across"),
)
SPOT_IDS = tuple(s.id for s in PHOTO_SPOTS)


def spot(spot_id: str) -> PhotoSpot:
    for s in PHOTO_SPOTS:
        if s.id == spot_id:
            return s
    raise KeyError(f"unknown photo spot {spot_id!r}; have {', '.join(SPOT_IDS)}")


def spot_camera(s: PhotoSpot, scene: SceneSpec, camera_id: str | None = None) -> CameraSpec:
    """The spot as a scene camera (clean quality, the recipe's lens and resolution)."""
    top = scene.table.height
    eye = s.eye(top)
    target = (0.0, 0.0, top)
    if s.elevation_deg >= 89.999:
        # Straight down, top of the image toward the back edge (+y): the recipe's overhead pose.
        rotation = (1.0, 0.0, 0.0, 0.0)
    else:
        rotation = look_at_camera_quat(eye, target)
    cid = camera_id or s.id
    return CameraSpec(
        id=cid,
        rig=s.rig,  # type: ignore[arg-type]
        pose={"position": round_vec(eye), "rotation": round_vec(rotation)},
        focal_mm=FOCAL_MM,
        sensor_width_mm=SENSOR_WIDTH_MM,
        resolution=RESOLUTION,
        quality=CameraQuality(seed=derive_seed(scene.meta.seed, "twin_camera", cid)),
    )


def twin_scene(
    scene: SceneSpec, cameras: list[CameraSpec], scene_id: str | None = None
) -> SceneSpec:
    """The layout as photographed: printed mat on the table, no gripper, these cameras.

    Objects, lighting, physics, render settings and the seed are unchanged, so the settle (and
    every answer that does not depend on the camera) is the layout's own.
    """
    data = scene.model_dump(mode="json")
    data["table"]["material_id"] = TWIN_MAT_MATERIAL
    data["gripper"]["parts"] = []  # virtual: nothing to photograph
    # A lime dropped into the bowl is still rolling at the recipe's 90 frames (validate rejects
    # all four bowl layouts as moving_at_end); a real one stops, so the twin settles longer.
    data["physics"]["settle_frames"] = max(data["physics"]["settle_frames"], TWIN_SETTLE_FRAMES)
    data["cameras"] = [c.model_dump(mode="json") for c in cameras]
    difficulty = {
        k: v for k, v in data["meta"]["difficulty"].items() if not k.startswith("quality_")
    }
    difficulty.update({f"quality_{c.id}": c.quality.level for c in cameras})
    data["meta"]["difficulty"] = difficulty
    if scene_id:
        data["meta"]["scene_id"] = scene_id
    return SceneSpec.model_validate(data)


def digital_twin_scene(scene: SceneSpec) -> SceneSpec:
    return twin_scene(scene, [spot_camera(s, scene) for s in PHOTO_SPOTS])


# Negative control for G8 ----------------------------------------------------------------------


def _footprint_xy(scene: SceneSpec, object_id: str) -> np.ndarray:
    from vtf.twin.layouts import _footprint

    return _footprint(scene.object(object_id))


def _poly_gap(a: np.ndarray, b: np.ndarray) -> float:
    """Smallest distance between two convex polygons (0 when they overlap)."""
    pa, pb = a.astype(np.float32), b.astype(np.float32)
    if any(cv2.pointPolygonTest(pb, (float(x), float(y)), False) >= 0 for x, y in pa) or any(
        cv2.pointPolygonTest(pa, (float(x), float(y)), False) >= 0 for x, y in pb
    ):
        return 0.0
    d = [abs(cv2.pointPolygonTest(pb, (float(x), float(y)), True)) for x, y in pa]
    d += [abs(cv2.pointPolygonTest(pa, (float(x), float(y)), True)) for x, y in pb]
    return float(min(d))


def pick_misplacement(
    scene: SceneSpec, shift_m: float = MISPLACE_SHIFT_M
) -> tuple[str, tuple[float, float]] | None:
    """First table-standing object (in id order) and direction (+x, -x, +y, -y) such that shifting
    it by shift_m keeps its footprint inside the placement area and clear of every other object by
    MISPLACE_MIN_GAP_M."""
    area = default_mat_spec().placement_area
    feet = {o.id: _footprint_xy(scene, o.id) for o in scene.objects}
    children = {o.placement.parent_id for o in scene.objects if o.placement.parent_id}
    for obj in sorted(scene.objects, key=lambda o: o.id):
        if obj.placement.kind != "table" or obj.id in children:
            continue
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            shift = np.array(d, dtype=float) * shift_m
            moved = feet[obj.id] + shift
            inside = all(area.contains(float(x), float(y)) for x, y in moved)
            gaps = [_poly_gap(moved, f) for oid, f in feet.items() if oid != obj.id]
            if inside and min(gaps, default=1.0) >= MISPLACE_MIN_GAP_M:
                return obj.id, (float(shift[0]), float(shift[1]))
    return None


def misplaced_scene(scene: SceneSpec, object_id: str, shift: tuple[float, float]) -> SceneSpec:
    data = scene.model_dump(mode="json")
    for obj in data["objects"]:
        if obj["id"] == object_id:
            x, y, z = obj["pose"]["position"]
            obj["pose"]["position"] = list(round_vec((x + shift[0], y + shift[1], z)))
    return SceneSpec.model_validate(data)


# The mat texture ------------------------------------------------------------------------------


def write_mat_texture(pdf: Path, out: Path) -> Path:
    """docs/twins/mat_texture.png: the A3 mat PDF's grid area (40 x 28 cm) at 4 px/mm, rasterised
    with poppler's pdftoppm at 8 px/mm and area-averaged, so the render shows the printed mat.

    Pixel (row i, column j) covers mat x = j / 4 - 200 mm .. +1/4 mm from the left (front-left
    corner at the bottom-left of the image, back edge at the top).
    """
    exe = shutil.which("pdftoppm")
    if exe is None:
        raise FileNotFoundError("pdftoppm (poppler) is needed: brew install poppler")
    dpi = RENDER_PX_PER_MM * 25.4
    with tempfile.TemporaryDirectory() as tmp:
        stem = Path(tmp) / "mat"
        subprocess.run(
            [exe, "-r", f"{dpi:.1f}", "-png", "-singlefile", "-aa", "yes", "-aaVector", "yes",
             str(pdf), str(stem)],
            check=True,
        )  # fmt: skip
        page = cv2.imread(str(stem.with_suffix(".png")), cv2.IMREAD_COLOR)
    page_w_mm, page_h_mm = 420.0, 297.0  # A3 landscape, the mat centred on it
    s = RENDER_PX_PER_MM
    x0 = int(round((page_w_mm - GRID_MM[0]) / 2 * s))
    y0 = int(round((page_h_mm - GRID_MM[1]) / 2 * s))
    crop = page[y0 : y0 + int(GRID_MM[1] * s), x0 : x0 + int(GRID_MM[0] * s)]
    size = (int(GRID_MM[0] * TEXTURE_PX_PER_MM), int(GRID_MM[1] * TEXTURE_PX_PER_MM))
    tex = cv2.resize(crop, size, interpolation=cv2.INTER_AREA)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), tex)
    print(f"vtf twin texture: {out} ({tex.shape[1]} x {tex.shape[0]} px, 4 px/mm)")
    return out


def texture_to_mat() -> np.ndarray:
    """3x3 matrix: texture pixel (u, v, 1), OpenCV pixel centres, -> mat metres (x, y, 1)."""
    k = 1.0 / (TEXTURE_PX_PER_MM * 1000)
    return np.array(
        [[k, 0.0, 0.5 * k - GRID_MM[0] / 2000],
         [0.0, -k, GRID_MM[1] / 2000 - 0.5 * k],
         [0.0, 0.0, 1.0]]
    )  # fmt: skip


def default_texture_path() -> Path:
    return Path(__file__).resolve().parents[5] / "docs" / "twins" / TEXTURE_FILE


# vtf twin digital -----------------------------------------------------------------------------


def selection_ids(selection: dict) -> list[str]:
    return list(selection["scene_ids"])


def layout_label(index: int) -> str:
    """L01, L02, ... in selection order (the order of the layout sheets)."""
    return f"L{index + 1:02d}"


def make_digital_twins(src_run: Path, selection: dict, out: Path) -> list[Path]:
    """Derived scene files for every selected layout, plus the misplaced negative control."""
    src = RunLayout(src_run)
    dst = RunLayout(out)
    written = []
    ids = selection_ids(selection)
    for sid in ids:
        scene = read_scene(src.scene_file(sid))
        written.append(write_model(dst.scene_file(sid), digital_twin_scene(scene)))
    # Negative control: the first layout (in selection order) with a clean 4 cm move.
    neg = RunLayout(Path(out) / MISPLACED_RUN)
    control = None
    for sid in ids:
        scene = read_scene(src.scene_file(sid))
        pick = pick_misplacement(scene)
        if pick is None:
            continue
        object_id, shift = pick
        moved = digital_twin_scene(misplaced_scene(scene, object_id, shift))
        written.append(write_model(neg.scene_file(sid), moved))
        control = {
            "scene_id": sid,
            "object_id": object_id,
            "object_name": scene.object(object_id).name,
            "shift_m": list(shift),
            "note": "Rendered as if it were a photo of layout " + sid + "; G8 must reject it.",
        }
        break
    if control is None:
        raise ValueError("no layout has an object that can move 4 cm cleanly")
    (neg.root / "misplacement.json").write_text(json.dumps(control, indent=2) + "\n")
    print(
        f"vtf twin digital: {len(ids)} layouts -> {dst.scenes_dir}; negative control "
        f"{control['scene_id']} {control['object_name']} moved {control['shift_m']} m -> "
        f"{neg.scenes_dir}"
    )
    return written
