"""Layout sheets: one printable top-down page per twin scene, read straight from the scene files.

`vtf twin layouts <run> --out docs/twins/layouts.pdf` writes a cover page with instructions, then
one A4 portrait page per scene: the mat outline, grid and markers; every object's footprint (the
convex hull of its mesh projected onto the table) with its number, name and centre in cm; an
arrow along each object's local x axis with the turn angle; the camera spots with height and aim;
and a table with the same numbers for placing by hand.

Positions come from render/scene.settled.json when the scene has been rendered (that file is the
truth the questions use), else from scene.json as sampled; the sheet says which. Scenes that need
more copies of an asset than the shopping list buys are skipped and listed on the cover.
"""

import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.pdfgen.canvas import FILL_NON_ZERO

from vtf.core.assets import object_mesh
from vtf.core.geometry import quat_to_matrix
from vtf.core.io import read_scene
from vtf.core.layout import RunLayout
from vtf.core.scene import CameraSpec, SceneObject, SceneSpec
from vtf.twin.mat import (
    BLUE,
    GRID_MM,
    PLACEMENT_MM,
    PT,
    MatSpec,
    default_mat_spec,
    marker_cells,
)
from vtf.twin.shopping import build_shopping_list

PAGE_W, PAGE_H = (v / mm for v in A4)  # millimetres
MARGIN = 12.0
VIEW_X = (-220.0, 220.0)  # drawing window in mat mm: the mat plus room for camera arrows
VIEW_Y = (-230.0, 165.0)
SCALE = (PAGE_W - 2 * MARGIN) / (VIEW_X[1] - VIEW_X[0])  # page mm per mat mm
DRAW_TOP = PAGE_H - MARGIN - 17.0
SYMMETRIC = {"cylinder", "sphere", "cone"}


@dataclass(frozen=True)
class ObjectPlacement:
    number: int
    obj: SceneObject
    centre_cm: tuple[float, float]
    footprint_m: np.ndarray  # (K, 2) convex hull on the table plane
    turn_deg: float | None  # local x axis heading, counter-clockwise from +x; None if symmetric
    tilt_deg: float  # local z axis away from world up
    base_cm: float  # lowest point above the table top
    note: str


@dataclass(frozen=True)
class CameraSpot:
    camera: CameraSpec
    lens_cm: tuple[float, float]
    height_cm: float
    aim_cm: tuple[float, float] | None
    tilt_down_deg: float
    heading_deg: float


def _fmt(v: float) -> str:
    return f"{v:+.1f}".replace("+0.0", "0.0").replace("-0.0", "0.0")


def _srgb(linear: tuple[float, float, float]) -> tuple[float, float, float]:
    def f(c: float) -> float:
        return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055

    return tuple(min(1.0, max(0.0, f(c))) for c in linear)  # type: ignore[return-value]


def _footprint(obj: SceneObject) -> np.ndarray:
    try:
        pts = np.asarray(object_mesh(obj).vertices)[:, :2]
    except Exception:  # missing asset file: fall back to the bounding box
        half = np.asarray(obj.dimensions) / 2
        signs = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
        pts = (signs * half) @ obj.pose.rotation_matrix().T + np.asarray(obj.pose.position)
        pts = pts[:, :2]
    hull = cv2.convexHull(pts.astype(np.float32)).reshape(-1, 2)
    return hull.astype(np.float64)


def object_placements(scene: SceneSpec) -> list[ObjectPlacement]:
    top = scene.table.height
    names = {o.id: o.name for o in scene.objects}
    out = []
    for n, obj in enumerate(scene.objects, 1):
        rot = quat_to_matrix(obj.pose.rotation)
        tilt = math.degrees(math.acos(max(-1.0, min(1.0, rot[2, 2]))))
        symmetric = obj.source == "primitive" and obj.shape in SYMMETRIC and tilt < 1.0
        turn = None if symmetric else math.degrees(math.atan2(rot[1, 0], rot[0, 0]))
        half = np.asarray(obj.dimensions) / 2
        base = obj.pose.position[2] - float(np.abs(rot[2, :]) @ half) - top
        notes = []
        if obj.placement.kind in ("in", "on") and obj.placement.parent_id:
            parent = obj.placement.parent_id
            notes.append(f"{obj.placement.kind} {names.get(parent, parent)}")
        notes.append("upright" if tilt < 10 else f"tipped {tilt:.0f} deg")
        out.append(
            ObjectPlacement(
                number=n,
                obj=obj,
                centre_cm=(obj.pose.position[0] * 100, obj.pose.position[1] * 100),
                footprint_m=_footprint(obj),
                turn_deg=turn,
                tilt_deg=tilt,
                base_cm=base * 100,
                note=", ".join(notes),
            )
        )
    return out


def camera_spots(scene: SceneSpec) -> list[CameraSpot]:
    top = scene.table.height
    spots = []
    for cam in scene.cameras:
        rot = quat_to_matrix(cam.pose.rotation)
        view = -rot[:, 2]
        lens = np.asarray(cam.pose.position)
        aim = None
        if view[2] < -1e-6:
            t = (top - lens[2]) / view[2]
            hit = lens + t * view
            aim = (hit[0] * 100, hit[1] * 100)
        spots.append(
            CameraSpot(
                camera=cam,
                lens_cm=(lens[0] * 100, lens[1] * 100),
                height_cm=(lens[2] - top) * 100,
                aim_cm=aim,
                tilt_down_deg=math.degrees(math.asin(max(-1.0, min(1.0, -view[2])))),
                heading_deg=math.degrees(math.atan2(view[1], view[0])),
            )
        )
    return spots


def load_layout_scene(run: RunLayout, scene_id: str) -> tuple[SceneSpec, bool]:
    settled = run.settled_scene(scene_id)
    if settled.exists():
        return read_scene(settled), True
    return read_scene(run.scene_file(scene_id)), False


# Drawing --------------------------------------------------------------------------------------


def _p(x_mm: float, y_mm: float) -> tuple[float, float]:
    """Mat millimetres -> page millimetres."""
    return MARGIN + (x_mm - VIEW_X[0]) * SCALE, DRAW_TOP - (VIEW_Y[1] - y_mm) * SCALE


def _font(c: rl_canvas.Canvas, size_pt: float, bold: bool = False) -> None:
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size_pt * PT)


def _draw_mat(c: rl_canvas.Canvas, spec: MatSpec) -> None:
    hx, hy = GRID_MM[0] / 2, GRID_MM[1] / 2
    for i in range(-20, 21):
        grey = 0.55 if i % 10 == 0 else 0.72 if i % 5 == 0 else 0.88
        c.setStrokeGray(grey)
        c.setLineWidth((0.5 if i % 5 == 0 else 0.25) * PT)
        c.line(*_p(i * 10, -hy), *_p(i * 10, hy))
    for j in range(-14, 15):
        grey = 0.55 if j % 10 == 0 else 0.72 if j % 5 == 0 else 0.88
        c.setStrokeGray(grey)
        c.setLineWidth((0.5 if j % 5 == 0 else 0.25) * PT)
        c.line(*_p(-hx, j * 10), *_p(hx, j * 10))
    c.setStrokeGray(0.2)
    c.setLineWidth(0.8 * PT)
    c.line(*_p(-hx, 0), *_p(hx, 0))
    c.line(*_p(0, -hy), *_p(0, hy))
    x0, y0 = _p(-hx, -hy)
    x1, y1 = _p(hx, hy)
    c.setStrokeGray(0.0)
    c.setLineWidth(1.2 * PT)
    c.rect(x0, y0, x1 - x0, y1 - y0, stroke=1, fill=0)
    px, py = PLACEMENT_MM[0] / 2, PLACEMENT_MM[1] / 2
    a0, b0 = _p(-px, -py)
    a1, b1 = _p(px, py)
    c.setStrokeColorRGB(*BLUE)
    c.setDash(2, 1.5)
    c.rect(a0, b0, a1 - a0, b1 - b0, stroke=1, fill=0)
    c.setDash()
    size = spec.marker_size_m * 1000
    for m in spec.markers:
        cx, cy = (v * 1000 for v in m.center_xy)
        cell = size / 6
        cells = marker_cells(m.id)
        c.setFillGray(0.0)
        path = c.beginPath()
        for row in range(6):
            for col in range(6):
                if cells[row, col]:
                    u, v = _p(cx - size / 2 + col * cell, cy + size / 2 - (row + 1) * cell)
                    path.rect(u, v, cell * SCALE, cell * SCALE)
        c.drawPath(path, stroke=0, fill=1, fillMode=FILL_NON_ZERO)
    _font(c, 6.5)
    c.setFillGray(0.25)
    for cm in range(-20, 21, 5):
        u, v = _p(cm * 10, -hy)
        c.drawCentredString(u, v - 3, f"{cm:+d}" if cm else "0")
    for cm in range(-10, 11, 5):
        u, v = _p(-hx, cm * 10)
        c.drawRightString(u - 1.2, v - 0.8, f"{cm:+d}" if cm else "0")
    _font(c, 7, bold=True)
    c.setFillGray(0.0)
    u, v = _p(0, hy)
    c.drawCentredString(u, v + 1.5, "BACK EDGE")
    u, v = _p(0, -hy)
    c.drawCentredString(u, v - 7, "FRONT EDGE (x, y in cm from the mat centre)")


def _arrow(c: rl_canvas.Canvas, x0: float, y0: float, x1: float, y1: float, head: float) -> None:
    c.line(x0, y0, x1, y1)
    ang = math.atan2(y1 - y0, x1 - x0)
    for side in (-1, 1):
        a = ang + math.pi + side * math.radians(25)
        c.line(x1, y1, x1 + head * math.cos(a), y1 + head * math.sin(a))


def _draw_objects(c: rl_canvas.Canvas, placements: list[ObjectPlacement]) -> None:
    order = sorted(placements, key=lambda p: (p.obj.pose.position[2], p.number))
    for p in order:
        rgb = _srgb(p.obj.rgb)
        dark = tuple(0.6 * v for v in rgb)
        path = c.beginPath()
        for k, (x, y) in enumerate(p.footprint_m * 1000):
            u, v = _p(x, y)
            (path.moveTo if k == 0 else path.lineTo)(u, v)
        path.close()
        c.setFillColorRGB(*rgb, alpha=0.45)
        c.setStrokeColorRGB(*dark)
        c.setLineWidth(1.0 * PT)
        c.drawPath(path, stroke=1, fill=1)
    for p in order:
        cx, cy = (v * 10 for v in p.centre_cm)  # mm
        u, v = _p(cx, cy)
        c.setStrokeGray(0.0)
        c.setLineWidth(0.6 * PT)
        c.line(u - 1.2, v, u + 1.2, v)
        c.line(u, v - 1.2, u, v + 1.2)
    for p in order:
        u, v = _p(*(w * 10 for w in p.centre_cm))
        line1 = f"{p.number} {p.obj.name}"
        line2 = f"({_fmt(p.centre_cm[0])}, {_fmt(p.centre_cm[1])})"
        _font(c, 6.5, bold=True)
        w = max(c.stringWidth(line1), c.stringWidth(line2, "Helvetica", 6.5 * PT)) + 1.2
        c.setFillColorRGB(1, 1, 1, alpha=0.8)
        c.rect(u + 1.2, v - 5.6, w, 6.2, stroke=0, fill=1)
        c.setFillColorRGB(0, 0, 0, alpha=1.0)
        c.drawString(u + 1.8, v - 1.6, line1)
        _font(c, 6.5)
        c.drawString(u + 1.8, v - 4.6, line2)
    for p in order:  # arrows last, so labels never hide them
        if p.turn_deg is None:
            continue
        cx, cy = (v * 10 for v in p.centre_cm)
        reach = max(p.obj.dimensions[0] * 1000 / 2 + 8, 25)
        a = math.radians(p.turn_deg)
        c.setStrokeColorRGB(0.8, 0.1, 0.1)
        c.setLineWidth(1.0 * PT)
        _arrow(c, *_p(cx, cy), *_p(cx + reach * math.cos(a), cy + reach * math.sin(a)), 1.6)


def _clip_to_view(x: float, y: float, ax: float, ay: float) -> tuple[float, float, bool]:
    """Point (x, y) in mat mm, pulled along the line to (ax, ay) into the drawing window."""
    inside = VIEW_X[0] + 8 <= x <= VIEW_X[1] - 8 and VIEW_Y[0] + 8 <= y <= VIEW_Y[1] - 8
    if inside:
        return x, y, True
    t = 1.0
    bounds = ((VIEW_X[0] + 8, VIEW_X[1] - 8, x, ax), (VIEW_Y[0] + 8, VIEW_Y[1] - 8, y, ay))
    for lo, hi, p, q in bounds:
        if p < lo:
            t = min(t, (q - lo) / (q - p))
        elif p > hi:
            t = min(t, (hi - q) / (p - q))
    return ax + t * (x - ax), ay + t * (y - ay), False


def _draw_cameras(c: rl_canvas.Canvas, spots: list[CameraSpot]) -> None:
    for s in spots:
        lx, ly = (v * 10 for v in s.lens_cm)
        ax, ay = (v * 10 for v in s.aim_cm) if s.aim_cm else (lx, ly)
        px, py, inside = _clip_to_view(lx, ly, ax, ay)
        u0, v0 = _p(px, py)
        u1, v1 = _p(ax, ay)
        c.setStrokeColorRGB(0.1, 0.5, 0.2)
        c.setLineWidth(0.8 * PT)
        c.setDash(2, 1.2)
        c.line(u0, v0, u1, v1)
        c.setDash()
        c.line(u1 - 1.5, v1 - 1.5, u1 + 1.5, v1 + 1.5)
        c.line(u1 - 1.5, v1 + 1.5, u1 + 1.5, v1 - 1.5)
        c.setFillColorRGB(0.1, 0.5, 0.2)
        if inside:
            c.rect(u0 - 2.5, v0 - 1.8, 5, 3.6, stroke=1, fill=1)
        else:  # arrow head pointing out toward the lens, which is off the drawing
            c.setLineWidth(1.4 * PT)
            ang = math.atan2(v0 - v1, u0 - u1)
            _arrow(c, u0 - 6 * math.cos(ang), v0 - 6 * math.sin(ang), u0, v0, 2.2)
        _font(c, 7, bold=True)
        if inside:  # over the mat: a short tag only, so it never hides objects
            c.drawString(u0 + 3.0, v0 - 1.0, f"{s.camera.id} {s.height_cm:.0f} cm up")
            continue
        label = f"{s.camera.id} camera: lens {s.height_cm:.1f} cm up"
        off = 3.0 if u0 < PAGE_W / 2 else -3.0
        draw = c.drawString if off > 0 else c.drawRightString
        draw(u0 + off, v0 + 1.5, label)
        _font(c, 6.5)
        draw(
            u0 + off, v0 - 1.8,
            f"at ({_fmt(s.lens_cm[0])}, {_fmt(s.lens_cm[1])}), {_dist_cm(s)} cm off the drawing",
        )  # fmt: skip


def _dist_cm(s: CameraSpot) -> str:
    lx, ly = s.lens_cm
    px, py, _ = _clip_to_view(lx * 10, ly * 10, *(v * 10 for v in (s.aim_cm or s.lens_cm)))
    return f"{math.hypot(lx - px / 10, ly - py / 10):.0f}"


def _camera_rows(spots: list[CameraSpot]) -> list[str]:
    rows = []
    hy = GRID_MM[1] / 20
    for s in spots:
        cam = s.camera
        lx, ly = s.lens_cm
        if ly < -hy:
            edge = f", {-hy - ly:.1f} cm in front of the front edge"
        elif ly > hy:
            edge = f", {ly - hy:.1f} cm behind the back edge"
        else:
            edge = ", over the mat"
        aim = f"aim at ({_fmt(s.aim_cm[0])}, {_fmt(s.aim_cm[1])})" if s.aim_cm else "level"
        if s.tilt_down_deg > 85:
            look = "pointing straight down, top of the phone toward the back edge"
        else:
            look = f"tilted down {s.tilt_down_deg:.0f} deg, heading {s.heading_deg:.0f} deg"
        w, h = cam.resolution
        shape = "landscape" if w >= h else "portrait"
        rows.append(
            f"{cam.id} ({cam.rig}): lens at ({_fmt(lx)}, {_fmt(ly)}) cm{edge}, "
            f"{s.height_cm:.1f} cm above the table; {aim}; {look}; {shape}, "
            f"{cam.focal_mm:.0f} mm on {cam.sensor_width_mm:.0f} mm (phone 1x is about 26 mm)."
        )
    return rows


def _object_rows(placements: list[ObjectPlacement]) -> list[tuple[str, ...]]:
    rows = []
    for p in placements:
        d = p.obj.dimensions
        turn = "any" if p.turn_deg is None else f"{p.turn_deg:+.0f} deg"
        size = " x ".join(f"{v * 100:.1f}" for v in d)
        rows.append(
            (str(p.number), p.obj.name, p.obj.shape,
             f"({_fmt(p.centre_cm[0])}, {_fmt(p.centre_cm[1])})", turn, size, p.note)
        )  # fmt: skip
    return rows


COLUMNS = (("#", 0), ("object", 5), ("asset", 30), ("centre x, y cm", 68), ("turn", 94),
           ("size x, y, z cm", 110), ("notes", 140))  # fmt: skip


def _draw_scene_page(
    c: rl_canvas.Canvas, scene: SceneSpec, settled: bool, spec: MatSpec, run_name: str
) -> None:
    placements = object_placements(scene)
    spots = camera_spots(scene)
    c.saveState()
    c.scale(mm, mm)
    c.setFillGray(0.0)
    _font(c, 14, bold=True)
    c.drawString(MARGIN, PAGE_H - MARGIN - 5, f"Twin layout {scene.meta.scene_id}")
    _font(c, 7.5)
    source = (
        "positions from the settled scene (render/scene.settled.json), the truth the questions use"
        if settled
        else "positions as sampled (scene.json); not yet settled, the settle may move objects a "
        "few mm"
    )
    c.drawString(
        MARGIN, PAGE_H - MARGIN - 10,
        f"run {run_name}  •  seed {scene.meta.seed}  •  recipe "
        f"{scene.meta.recipe_hash[:8]}  •  {scene.meta.generator_version}  •  "
        f"{len(scene.objects)} objects",
    )  # fmt: skip
    c.drawString(MARGIN, PAGE_H - MARGIN - 14, source + ".")
    _draw_mat(c, spec)
    _draw_objects(c, placements)
    _draw_cameras(c, spots)
    # Table.
    c.setFillColorRGB(0, 0, 0, alpha=1.0)
    y = _p(0, VIEW_Y[0])[1] - 4
    _font(c, 9, bold=True)
    c.drawString(MARGIN, y, "Objects (centre = middle of the object's box, on the mat; turn = "
                 "red arrow, counter-clockwise from +x)")  # fmt: skip
    y -= 5
    _font(c, 7, bold=True)
    for title, x in COLUMNS:
        c.drawString(MARGIN + x, y, title)
    _font(c, 7)
    for row in _object_rows(placements):
        y -= 4
        for (_, x), text in zip(COLUMNS, row, strict=True):
            c.drawString(MARGIN + x, y, text)
    y -= 7
    _font(c, 9, bold=True)
    c.drawString(MARGIN, y, "Camera spots")
    _font(c, 7)
    for row in _camera_rows(spots):
        for part in _wrap(c, row, PAGE_W - 2 * MARGIN):
            y -= 4
            c.drawString(MARGIN, y, part)
    tip = scene.gripper.tip.position
    y -= 6
    for part in _wrap(
        c,
        f"The render also shows a gripper with its fingertips at ({_fmt(tip[0] * 100)}, "
        f"{_fmt(tip[1] * 100)}) cm, {(tip[2] - scene.table.height) * 100:.1f} cm above the "
        "table. There is no physical gripper: leave that spot empty; gripper questions are not "
        "asked on photos.",
        PAGE_W - 2 * MARGIN,
    ):
        c.drawString(MARGIN, y, part)
        y -= 4
    c.restoreState()


def _wrap(c: rl_canvas.Canvas, text: str, width: float) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if c.stringWidth(trial) > width and line:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line] if line else lines


COVER_STEPS = (
    "Print the mat at 100% (docs/twins/mat_A3.pdf, or the four A4 tiles in mat_A4_tiled.pdf "
    "taped together). Check that both 10 cm bars measure exactly 10 cm.",
    "Tape the mat flat on a table, front edge toward you. Keep the four corner markers "
    "uncovered and in every photo.",
    "Set the phone at each camera spot on the sheet: the front spot on a stand or a stack of "
    "books (lens height above the table, distance in front of the front edge, aimed at the "
    "marked point); the overhead spot straight above the mat centre, screen level, top of the "
    "phone toward the back edge. Use the 1x camera, landscape, no zoom. The spots are the same "
    "for every scene.",
    "For each page: place every object with its centre on the given (x, y) in cm and its red "
    "arrow pointing as drawn. Colours and names match the shopping list.",
    "Take one photo per camera spot. Name it <scene_id>_<camera_id>.jpg (for example "
    "scene_0007_front.jpg) and drop it in the photos/ folder of this run.",
    "Clear the mat and go to the next page. Each photo is registered onto the mat by the "
    "markers (gate G8), so small phone movements are fine.",
)


def _draw_cover(
    c: rl_canvas.Canvas,
    run_name: str,
    scenes: list[tuple[SceneSpec, bool]],
    skipped: list[tuple[str, str]],
) -> None:
    c.saveState()
    c.scale(mm, mm)
    y = PAGE_H - MARGIN - 8
    _font(c, 18, bold=True)
    c.drawString(MARGIN, y, "Physical-twin layout sheets")
    y -= 8
    _font(c, 9)
    n_obj = sum(len(s.objects) for s, _ in scenes)
    n_settled = sum(settled for _, settled in scenes)
    c.drawString(MARGIN, y, f"Run {run_name}: {len(scenes)} scenes, {n_obj} objects; "
                 f"{n_settled} of {len(scenes)} from settled scene files.")  # fmt: skip
    y -= 10
    _font(c, 11, bold=True)
    c.drawString(MARGIN, y, "How to use")
    _font(c, 9)
    for i, step in enumerate(COVER_STEPS, 1):
        for k, part in enumerate(_wrap(c, step, PAGE_W - 2 * MARGIN - 8)):
            y -= 5
            if k == 0:
                c.drawString(MARGIN, y, f"{i}.")
            c.drawString(MARGIN + 6, y, part)
        y -= 2
    if skipped:
        y -= 4
        _font(c, 8)
        text = "Skipped, need more than the shopping list: " + "; ".join(
            f"{sid} ({why})" for sid, why in skipped
        )
        for part in _wrap(c, text, PAGE_W - 2 * MARGIN):
            y -= 4.2
            c.drawString(MARGIN, y, part)
    y -= 10
    _font(c, 11, bold=True)
    c.drawString(MARGIN, y, "Scenes")
    _font(c, 8)
    for i, (s, _) in enumerate(scenes):
        col, row = divmod(i, 40)
        names = ", ".join(sorted({o.category for o in s.objects}))
        text = f"p.{i + 2}  {s.meta.scene_id}: {len(s.objects)} objects ({names})"
        c.drawString(MARGIN + col * 95, y - 4.2 * (row + 1), text[:60])
    c.restoreState()


def shortfall(scene: SceneSpec, quantities: dict[str, int]) -> str:
    """What a scene needs beyond the bought quantities ('' when it can be built)."""
    need = Counter(o.shape for o in scene.objects)
    short = [f"{n} x {a}" for a, n in sorted(need.items()) if n > quantities.get(a, 0)]
    return ", ".join(short)


def make_layouts(
    run_dir: Path,
    out: Path,
    limit: int | None = None,
    buildable_only: bool = True,
    scene_ids: list[str] | None = None,
    quantities: dict[str, int] | None = None,
) -> Path:
    """`vtf twin layouts`: cover page plus one layout page per scene of the run.

    With `buildable_only`, scenes that need more copies of an asset than the shopping list buys
    (or an asset not on it) are skipped and listed on the cover; `limit` counts kept scenes.
    `scene_ids` (a selection, docs/twins/selection.json) prints only those scenes, in that order,
    and `quantities` replaces the full shopping list's.
    """
    run = RunLayout(run_dir)
    all_ids = list(scene_ids) if scene_ids else run.scene_ids()
    if not all_ids:
        raise FileNotFoundError(f"no scenes in {run.scenes_dir}")
    if quantities is None:
        quantities = {it.asset_id: it.quantity for it in build_shopping_list()}
    scenes: list[tuple[SceneSpec, bool]] = []
    skipped: list[tuple[str, str]] = []
    for sid in all_ids:
        if limit and len(scenes) >= limit:
            break
        scene, settled = load_layout_scene(run, sid)
        why = shortfall(scene, quantities) if buildable_only else ""
        if why:
            skipped.append((sid, why))
        else:
            scenes.append((scene, settled))
    if not scenes:
        raise ValueError(f"no scene in {run.scenes_dir} can be built from the shopping list")
    spec = default_mat_spec()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    c = rl_canvas.Canvas(str(out), pagesize=A4, invariant=1)
    c.setTitle("VTF physical-twin layout sheets")
    c.setAuthor("Visual Task Factory (vtf twin)")
    run_name = Path(run_dir).name
    _draw_cover(c, run_name, scenes, skipped)
    c.showPage()
    for scene, settled in scenes:
        _draw_scene_page(c, scene, settled, spec, run_name)
        c.showPage()
    c.save()
    print(
        f"vtf twin layouts: {len(scenes)} scenes ({len(skipped)} skipped, not buildable from "
        f"the shopping list) -> {out} ({out.stat().st_size:,} bytes)"
    )
    return out
