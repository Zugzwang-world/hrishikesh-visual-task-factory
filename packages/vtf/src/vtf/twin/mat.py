"""Placement mat for physical twins: geometry, vector PDFs (A3 sheet, tiled A4) and a raster.

The founder prints the mat, lays real objects on it exactly where a layout sheet says, and
photographs it. Four ArUco markers (OpenCV DICT_4X4_50, ids 0 to 3) in the corners let gate G8 map
every photo onto the mat (`vtf.twin.register`).

Mat frame = the scene's world frame on the table top: origin at the mat centre (the table centre
in the scene), +x to the right seen from the front edge, +y away from the front edge (toward the
back). mat_spec.json is in metres like the scene; the drawing code below works in millimetres.

Print layout (mm, mat frame). The grid covers 400 x 280, the twin recipe's table. Each marker is
a 30 mm square (5 mm cells) with a 5 mm white quiet zone that reaches the grid corner, so the
corner squares |x| >= 160, |y| >= 100 are kept free. Objects go inside the placement area
(310 x 190, a dashed blue outline), clear of the markers and of the text strips along the edges:
the print note and a 10 cm scale bar at the front, the title at the back, a second scale bar on
the right (printers can scale x and y differently).
"""

import math
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from reportlab.lib.pagesizes import A3, A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.pdfgen.canvas import FILL_NON_ZERO

MAT_SPEC_VERSION = "0.1.0"
ARUCO_DICT_NAME = "DICT_4X4_50"
ARUCO_DICT = cv2.aruco.DICT_4X4_50

GRID_MM = (400.0, 280.0)
GRID_CELL_MM = 10.0
MARKER_MM = 30.0
QUIET_MM = 5.0  # one marker cell
PLACEMENT_MM = (310.0, 190.0)
SCALE_BAR_MM = 100.0
CORNERS = {0: ("back-left", -1, 1), 1: ("back-right", 1, 1), 2: ("front-right", 1, -1),
           3: ("front-left", -1, -1)}  # fmt: skip

# Tiled A4: two cuts near the centre (between grid lines), 15 mm overlap. The right column and
# the back row are trimmed at the cut and laid over their neighbours.
CUT_MM = (5.0, 5.0)
OVERLAP_MM = 15.0
BLEED_MM = 2.0  # window reaches this far past the grid edge so the border line prints whole
ALIGN_MARKS_X_CUT = (-85.0, -40.0, 45.0, 90.0)  # y positions of crosses on the x cut
ALIGN_MARKS_Y_CUT = (-150.0, -80.0, 80.0, 150.0)  # x positions of crosses on the y cut

A3_FILE = "mat_A3.pdf"
A4_FILE = "mat_A4_tiled.pdf"
SPEC_FILE = "mat_spec.json"

FRAME_TEXT = (
    "Mat frame = scene world frame on the table top. Origin at the mat centre (the table "
    "centre); +x to the right seen from the front edge; +y away from the front edge, toward the "
    "back. Metres."
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Rect(_Model):
    x_min: float
    y_min: float
    x_max: float
    y_max: float

    def contains(self, x: float, y: float) -> bool:
        return self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max

    def intersects(self, other: "Rect") -> bool:
        return not (
            other.x_min >= self.x_max
            or other.x_max <= self.x_min
            or other.y_min >= self.y_max
            or other.y_max <= self.y_min
        )


class MarkerSpec(_Model):
    id: int
    corner: str = Field(description="back-left, back-right, front-right or front-left.")
    center_xy: tuple[float, float]
    corners_xy: list[tuple[float, float]] = Field(
        description="Marker corners in OpenCV's order (top-left, top-right, bottom-right, "
        "bottom-left of the marker image), seen from above with the back edge at the top."
    )


class TileSpec(_Model):
    page: int
    name: str
    window: Rect = Field(description="Part of the mat (mat frame, metres) printed on this page.")
    trim_left: bool = Field(description="Cut along the dashed left edge; lay over the left tile.")
    trim_bottom: bool = Field(description="Cut along the dashed bottom edge; lay over the front.")


class PrintSpec(_Model):
    file: str
    paper: str
    pages: int
    tiles: list[TileSpec] = Field(default_factory=list)


class MatSpec(_Model):
    mat_spec_version: str = MAT_SPEC_VERSION
    units: str = "m"
    frame: str = FRAME_TEXT
    size_xy: tuple[float, float] = Field(description="Grid size = the twin recipe's table size.")
    grid_cell_m: float
    grid_bold_every_m: tuple[float, float] = (0.05, 0.10)
    aruco_dictionary: str = ARUCO_DICT_NAME
    marker_size_m: float = Field(description="Black square edge, including the black border.")
    marker_quiet_zone_m: float
    markers: list[MarkerSpec]
    placement_area: Rect = Field(description="Objects stay inside this rectangle.")
    keep_out: list[Rect] = Field(description="Marker squares with their quiet zones.")
    scale_bar_m: float
    marker_0_to_2_m: float = Field(description="Check distance between marker 0 and 2 centres.")
    prints: list[PrintSpec]


def _r(v: float) -> float:
    return round(float(v), 6) + 0.0


def _marker_center_mm(marker_id: int) -> tuple[float, float]:
    _, sx, sy = CORNERS[marker_id]
    off = QUIET_MM + MARKER_MM / 2
    return sx * (GRID_MM[0] / 2 - off), sy * (GRID_MM[1] / 2 - off)


def _tiles_mm() -> list[tuple[str, tuple[float, float, float, float], bool, bool]]:
    hx, hy = GRID_MM[0] / 2 + BLEED_MM, GRID_MM[1] / 2 + BLEED_MM
    cx, cy = CUT_MM
    cols = [(-hx, cx + OVERLAP_MM, False), (cx, hx, True)]
    rows = [(-hy, cy + OVERLAP_MM, False, "front"), (cy, hy, True, "back")]
    tiles = []
    for y0, y1, trim_b, row_name in rows:
        for (x0, x1, trim_l), side in zip(cols, ("left", "right"), strict=True):
            tiles.append((f"{row_name}-{side}", (x0, y0, x1, y1), trim_l, trim_b))
    return tiles


def default_mat_spec() -> MatSpec:
    """The one mat both PDFs print. Pure function of the constants above."""
    half = MARKER_MM / 2
    markers = []
    keep_out = []
    for marker_id, (name, _, _) in CORNERS.items():
        cx, cy = _marker_center_mm(marker_id)
        corners = [(cx - half, cy + half), (cx + half, cy + half), (cx + half, cy - half),
                   (cx - half, cy - half)]  # fmt: skip
        markers.append(
            MarkerSpec(
                id=marker_id,
                corner=name,
                center_xy=(_r(cx / 1000), _r(cy / 1000)),
                corners_xy=[(_r(x / 1000), _r(y / 1000)) for x, y in corners],
            )
        )
        q = half + QUIET_MM
        keep_out.append(
            Rect(
                x_min=_r((cx - q) / 1000),
                y_min=_r((cy - q) / 1000),
                x_max=_r((cx + q) / 1000),
                y_max=_r((cy + q) / 1000),
            )  # fmt: skip
        )
    px, py = PLACEMENT_MM[0] / 2000, PLACEMENT_MM[1] / 2000
    c0, c2 = _marker_center_mm(0), _marker_center_mm(2)
    tiles = [
        TileSpec(
            page=i + 1,
            name=name,
            window=Rect(
                x_min=_r(x0 / 1000), y_min=_r(y0 / 1000), x_max=_r(x1 / 1000), y_max=_r(y1 / 1000)
            ),  # fmt: skip
            trim_left=trim_l,
            trim_bottom=trim_b,
        )
        for i, (name, (x0, y0, x1, y1), trim_l, trim_b) in enumerate(_tiles_mm())
    ]
    return MatSpec(
        size_xy=(_r(GRID_MM[0] / 1000), _r(GRID_MM[1] / 1000)),
        grid_cell_m=_r(GRID_CELL_MM / 1000),
        marker_size_m=_r(MARKER_MM / 1000),
        marker_quiet_zone_m=_r(QUIET_MM / 1000),
        markers=markers,
        placement_area=Rect(x_min=-px, y_min=-py, x_max=px, y_max=py),
        keep_out=keep_out,
        scale_bar_m=_r(SCALE_BAR_MM / 1000),
        marker_0_to_2_m=_r(math.dist(c0, c2) / 1000),
        prints=[
            PrintSpec(file=A3_FILE, paper="A3 landscape", pages=1),
            PrintSpec(file=A4_FILE, paper="A4 landscape", pages=len(tiles), tiles=tiles),
        ],
    )


def marker_cells(marker_id: int) -> np.ndarray:
    """(6, 6) bool, True = black, row 0 at the top of the marker (OpenCV's image layout)."""
    dictionary = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    img = cv2.aruco.generateImageMarker(dictionary, marker_id, 6, borderBits=1)
    return img < 128


# PDF drawing (canvas in mat millimetres: origin at the mat centre, +y toward the back) ---------

GREY_1CM, GREY_5CM, GREY_10CM = 0.78, 0.55, 0.30
BLUE = (0.10, 0.35, 0.80)
MAGENTA = (0.85, 0.10, 0.45)
PT = 25.4 / 72  # one point in millimetres


def _font(c: rl_canvas.Canvas, size_pt: float, bold: bool = False) -> None:
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size_pt * PT)


def _white_box(c: rl_canvas.Canvas, x0: float, y0: float, x1: float, y1: float) -> None:
    c.setFillGray(1.0)
    c.rect(x0, y0, x1 - x0, y1 - y0, stroke=0, fill=1)


def _draw_grid(c: rl_canvas.Canvas) -> None:
    hx, hy = GRID_MM[0] / 2, GRID_MM[1] / 2
    nx, ny = int(round(hx / GRID_CELL_MM)), int(round(hy / GRID_CELL_MM))
    for level in (1, 5, 10):  # light lines first, bold lines on top
        grey, width = {1: (GREY_1CM, 0.3), 5: (GREY_5CM, 0.6), 10: (GREY_10CM, 0.9)}[level]
        c.setStrokeGray(grey)
        c.setLineWidth(width * PT)
        for i in range(-nx, nx + 1):
            if _grid_level(i) == level and i != 0:
                c.line(i * GRID_CELL_MM, -hy, i * GRID_CELL_MM, hy)
        for j in range(-ny, ny + 1):
            if _grid_level(j) == level and j != 0:
                c.line(-hx, j * GRID_CELL_MM, hx, j * GRID_CELL_MM)
    c.setStrokeGray(0.0)
    c.setLineWidth(1.2 * PT)
    c.line(-hx, 0, hx, 0)
    c.line(0, -hy, 0, hy)
    c.setLineWidth(1.5 * PT)
    c.rect(-hx, -hy, 2 * hx, 2 * hy, stroke=1, fill=0)


def _grid_level(i: int) -> int:
    return 10 if i % 10 == 0 else 5 if i % 5 == 0 else 1


def _draw_placement_area(c: rl_canvas.Canvas) -> None:
    px, py = PLACEMENT_MM[0] / 2, PLACEMENT_MM[1] / 2
    c.setStrokeColorRGB(*BLUE)
    c.setLineWidth(1.0 * PT)
    c.setDash(3, 2)
    c.rect(-px, -py, 2 * px, 2 * py, stroke=1, fill=0)
    c.setDash()


def _draw_axis_labels(c: rl_canvas.Canvas) -> None:
    px, py = PLACEMENT_MM[0] / 2, PLACEMENT_MM[1] / 2
    _font(c, 7)
    c.setFillGray(0.15)
    for cm in range(-15, 16, 5):
        x = cm * 10.0
        label = f"{cm:+d}" if cm else "0"
        c.drawCentredString(x, -py - 3.6, label)  # under the front edge of the placement area
        c.drawCentredString(x, py + 1.4, label)  # over the back edge
        if cm and abs(x) < px:
            c.drawString(x + 0.8, 1.0, label)  # along the x axis
    for cm in range(-10, 11, 5):
        y = cm * 10.0
        label = f"{cm:+d}" if cm else "0"
        c.drawRightString(-px - 1.2, y - 1.0, label)
        c.drawString(px + 1.2, y - 1.0, label)
        if cm and abs(y) < py:
            c.drawString(1.0, y + 0.8, label)  # along the y axis
    _font(c, 8, bold=True)
    c.drawString(px - 12, 3.2, "+x cm")
    c.drawString(2.0, py - 6, "+y cm")


def _draw_marker(c: rl_canvas.Canvas, marker_id: int) -> None:
    cx, cy = _marker_center_mm(marker_id)
    half = MARKER_MM / 2
    q = half + QUIET_MM
    _white_box(c, cx - q, cy - q, cx + q, cy + q)
    cell = MARKER_MM / 6
    path = c.beginPath()
    cells = marker_cells(marker_id)
    for row in range(6):
        for col in range(6):
            if cells[row, col]:
                path.rect(cx - half + col * cell, cy + half - (row + 1) * cell, cell, cell)
    c.setFillGray(0.0)
    c.drawPath(path, stroke=0, fill=1, fillMode=FILL_NON_ZERO)  # cells merge: no seams
    name = CORNERS[marker_id][0]
    _font(c, 7)
    c.setFillGray(0.2)
    label = f"marker {marker_id} ({name})"
    ly = cy - q - 3.2 if cy > 0 else cy + q + 1.2
    if cx < 0:
        c.drawString(cx - q + 1, ly, label)
    else:
        c.drawRightString(cx + q - 1, ly, label)


def _draw_scale_bar(c: rl_canvas.Canvas, x0: float, y0: float, vertical: bool = False) -> None:
    """A 10 cm bar with 1 cm ticks starting at (x0, y0), along +x (or +y when vertical)."""
    c.saveState()
    c.translate(x0, y0)
    if vertical:
        c.rotate(90)
    _white_box(c, -4, -6.5, SCALE_BAR_MM + 4, 5.5)
    c.setStrokeGray(0.0)
    c.setFillGray(0.0)
    c.rect(0, 0, SCALE_BAR_MM, 1.6, stroke=0, fill=1)
    c.setLineWidth(0.5 * PT)
    for i in range(11):
        h = 4.0 if i in (0, 5, 10) else 2.8
        c.line(i * 10.0, 0, i * 10.0, h)
    _font(c, 7)
    c.drawString(0, -3.4, "0")
    c.drawRightString(SCALE_BAR_MM, -3.4, "10 cm")
    c.drawCentredString(SCALE_BAR_MM / 2, -3.4, "must measure exactly 100 mm")
    c.restoreState()


def _draw_text_strips(c: rl_canvas.Canvas, spec: MatSpec) -> None:
    hy = GRID_MM[1] / 2
    py = PLACEMENT_MM[1] / 2
    # Front strip: scale bar, print note, front edge.
    _draw_scale_bar(c, -SCALE_BAR_MM / 2, -py - 14)
    _white_box(c, -150, -hy + 3, 150, -py - 21.5)
    c.setFillColorRGB(0.75, 0.0, 0.0)
    _font(c, 10, bold=True)
    c.drawCentredString(0, -py - 27, "PRINT AT 100% (ACTUAL SIZE). NO SCALING, NO “FIT TO PAGE”.")
    c.setFillGray(0.0)
    _font(c, 8)
    c.drawCentredString(
        0, -py - 32.5,
        f"Check: both bars measure 10 cm; marker 0 to marker 2 centres = "
        f"{spec.marker_0_to_2_m * 100:.2f} cm.",
    )  # fmt: skip
    _font(c, 8, bold=True)
    c.drawCentredString(0, -hy + 4.5, "FRONT EDGE  •  phone / front camera side")
    # Back strip: title.
    _white_box(c, -150, py + 6.5, 150, hy - 3)
    c.setFillGray(0.0)
    _font(c, 11, bold=True)
    c.drawCentredString(0, hy - 11, "VTF physical-twin placement mat  •  40 × 28 cm")
    _font(c, 7.5)
    c.drawCentredString(
        0, hy - 17,
        "1 cm grid, bold every 5 and 10 cm; positions in cm from the centre (0, 0). Objects go "
        "inside the dashed blue area.",
    )  # fmt: skip
    c.drawCentredString(
        0, hy - 22,
        f"ArUco {ARUCO_DICT_NAME} markers 0–3, {MARKER_MM / 10:.0f} cm squares. Keep them "
        "uncovered and flat. Mat spec v" + spec.mat_spec_version + ".",
    )  # fmt: skip
    _font(c, 8, bold=True)
    c.drawCentredString(0, hy - 28.5, "BACK EDGE")
    # Right strip: vertical scale bar.
    _draw_scale_bar(c, GRID_MM[0] / 2 - 12, -SCALE_BAR_MM / 2, vertical=True)


def _draw_mat(c: rl_canvas.Canvas, spec: MatSpec) -> None:
    _draw_grid(c)
    _draw_placement_area(c)
    _draw_axis_labels(c)
    _draw_text_strips(c, spec)
    for marker_id in CORNERS:
        _draw_marker(c, marker_id)


def _new_canvas(path: Path, pagesize: tuple[float, float], title: str) -> rl_canvas.Canvas:
    c = rl_canvas.Canvas(str(path), pagesize=pagesize, invariant=1)  # byte-identical reruns
    c.setTitle(title)
    c.setAuthor("Visual Task Factory (vtf twin)")
    c.setCreator("vtf.twin.mat")
    return c


def write_mat_a3(path: Path, spec: MatSpec | None = None) -> Path:
    """One A3 landscape page with the mat centred on it."""
    spec = spec or default_mat_spec()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    page_w, page_h = landscape(A3)
    c = _new_canvas(path, (page_w, page_h), "VTF placement mat (A3)")
    c.translate(page_w / 2, page_h / 2)
    c.scale(mm, mm)
    _draw_mat(c, spec)
    c.showPage()
    c.save()
    return path


def _align_cross(c: rl_canvas.Canvas, x: float, y: float) -> None:
    c.setStrokeColorRGB(*MAGENTA)
    c.setLineWidth(0.5 * PT)
    c.circle(x, y, 3.0, stroke=1, fill=0)
    c.line(x - 6, y, x + 6, y)
    c.line(x, y - 6, x, y + 6)


def _tile_instructions(tile: TileSpec) -> list[str]:
    under = {"front-left": "", "front-right": "tile 1", "back-left": "tile 1",
             "back-right": "tiles 2 and 3"}[tile.name]  # fmt: skip
    if not under:
        return [
            "Lay this tile down first. The other tiles go over its shaded overlap strips.",
            "Assembly: 1, then 2 and 3 over 1, then 4 over 2 and 3.",
        ]
    edges = [e for e, on in (("left", tile.trim_left), ("bottom", tile.trim_bottom)) if on]
    edge_text = " and ".join(edges) + (" edges" if len(edges) > 1 else " edge")
    return [
        f"Cut along the dashed {edge_text}. Lay this tile over {under} so the magenta half "
        "crosses complete the crosses underneath",
        "and the grid lines run straight across the seam; tape from the back.",
    ]


def write_mat_a4_tiled(path: Path, spec: MatSpec | None = None) -> Path:
    """Four A4 landscape tiles of the same mat, with trim lines, overlap and alignment crosses."""
    spec = spec or default_mat_spec()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    page_w, page_h = landscape(A4)
    c = _new_canvas(path, (page_w, page_h), "VTF placement mat (4 x A4 tiles)")
    tiles = spec.prints[1].tiles
    for tile in tiles:
        win = tile.window
        x0, y0, x1, y1 = (v * 1000 for v in (win.x_min, win.y_min, win.x_max, win.y_max))
        w, h = x1 - x0, y1 - y0
        left = (page_w / mm - w) / 2  # window position on the page, mm
        bottom = page_h / mm - 12 - h
        c.saveState()
        c.scale(mm, mm)
        c.translate(left - x0, bottom - y0)  # mat frame on this page
        c.saveState()
        clip = c.beginPath()
        clip.rect(x0, y0, w, h)
        c.clipPath(clip, stroke=0, fill=0)
        _draw_mat(c, spec)
        # Overlap strips that the next tiles cover: light shading, then the alignment crosses.
        c.setFillColorRGB(0.85, 0.92, 1.0, alpha=0.45)
        if not tile.trim_left:
            c.rect(CUT_MM[0], y0, x1 - CUT_MM[0], h, stroke=0, fill=1)
        if not tile.trim_bottom:
            c.rect(x0, CUT_MM[1], w, y1 - CUT_MM[1], stroke=0, fill=1)
        for y in ALIGN_MARKS_X_CUT:
            _align_cross(c, CUT_MM[0], y)
        for x in ALIGN_MARKS_Y_CUT:
            _align_cross(c, x, CUT_MM[1])
        c.restoreState()
        c.setStrokeGray(0.0)
        c.setLineWidth(0.8 * PT)
        c.setDash(4, 2)
        if tile.trim_left:
            c.line(x0, y0 - 4, x0, y1 + 4)
        if tile.trim_bottom:
            c.line(x0 - 4, y0, x1 + 4, y0)
        c.setDash()
        c.restoreState()
        # Page furniture outside the window (page millimetres).
        c.saveState()
        c.scale(mm, mm)
        c.setFillGray(0.0)
        _font(c, 10, bold=True)
        c.drawString(left, bottom - 6, f"Tile {tile.page} of {len(tiles)}: {tile.name}")
        _font(c, 7.5)
        for i, line in enumerate(_tile_instructions(tile)):
            c.drawString(left, bottom - 11 - 4 * i, line)
        c.setFillColorRGB(0.75, 0.0, 0.0)
        _font(c, 8, bold=True)
        c.drawString(left, 9, "PRINT AT 100% (ACTUAL SIZE), NO SCALING.")
        c.restoreState()
        c.saveState()
        c.scale(mm, mm)
        _draw_scale_bar(c, page_w / mm - left - SCALE_BAR_MM, 12)
        c.restoreState()
        c.showPage()
    c.save()
    return path


def write_mat_spec(path: Path, spec: MatSpec | None = None) -> Path:
    spec = spec or default_mat_spec()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(spec.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def make_mat(out_dir: Path) -> list[Path]:
    """`vtf twin mat`: mat_A3.pdf, mat_A4_tiled.pdf and mat_spec.json in out_dir."""
    out_dir = Path(out_dir)
    spec = default_mat_spec()
    paths = [
        write_mat_a3(out_dir / A3_FILE, spec),
        write_mat_a4_tiled(out_dir / A4_FILE, spec),
        write_mat_spec(out_dir / SPEC_FILE, spec),
    ]
    for p in paths:
        print(f"vtf twin mat: {p} ({p.stat().st_size:,} bytes)")
    return paths


# Raster (tests and synthetic photos) ----------------------------------------------------------


def rasterize_mat(
    spec: MatSpec | None = None, px_per_mm: float = 2.0, margin_mm: float = 10.0
) -> tuple[np.ndarray, np.ndarray]:
    """Grey uint8 image of the mat (1 cm grid, markers with quiet zones) and the 3x3 matrix that
    maps mat metres (x, y, 1) to pixel coordinates (pixel centres at integers, OpenCV style).

    Marker edges land on pixel boundaries when px_per_mm * mm is an integer for every edge.
    """
    spec = spec or default_mat_spec()
    hx, hy = spec.size_xy[0] * 500, spec.size_xy[1] * 500
    s = px_per_mm
    width = int(round((2 * hx + 2 * margin_mm) * s))
    height = int(round((2 * hy + 2 * margin_mm) * s))
    world_to_px = np.array(
        [[1000 * s, 0.0, (hx + margin_mm) * s - 0.5],
         [0.0, -1000 * s, (hy + margin_mm) * s - 0.5],
         [0.0, 0.0, 1.0]]
    )  # fmt: skip
    img = np.full((height, width), 255, np.uint8)

    def px(x_mm: float, y_mm: float) -> tuple[int, int]:
        """Pixel boundary index of a mat point (boundary b sits at continuous b - 0.5)."""
        return int(round((x_mm + hx + margin_mm) * s)), int(round((hy + margin_mm - y_mm) * s))

    def fill(x0: float, y0: float, x1: float, y1: float, value: int) -> None:
        (u0, v1), (u1, v0) = px(x0, y0), px(x1, y1)
        img[v0:v1, u0:u1] = value

    cell = GRID_CELL_MM
    nx, ny = int(hx // cell), int(hy // cell)
    for i in range(-nx, nx + 1):
        u, v_top = px(i * cell, hy)
        _, v_bottom = px(i * cell, -hy)
        cv2.line(img, (u, v_top), (u, v_bottom), 100 if i % 5 == 0 else 190, 1)
    for j in range(-ny, ny + 1):
        u_left, v = px(-hx, j * cell)
        u_right, _ = px(hx, j * cell)
        cv2.line(img, (u_left, v), (u_right, v), 100 if j % 5 == 0 else 190, 1)
    size = spec.marker_size_m * 1000
    quiet = spec.marker_quiet_zone_m * 1000
    step = size / 6
    for marker in spec.markers:
        cx, cy = (v * 1000 for v in marker.center_xy)
        half = size / 2
        fill(cx - half - quiet, cy - half - quiet, cx + half + quiet, cy + half + quiet, 255)
        cells = marker_cells(marker.id)
        for row in range(6):
            for col in range(6):
                if cells[row, col]:
                    fill(cx - half + col * step, cy + half - (row + 1) * step,
                         cx - half + (col + 1) * step, cy + half - row * step, 0)  # fmt: skip
    return img, world_to_px
