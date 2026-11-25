"""Photo-session guide for the physical twins: docs/twins/photo_session.md and a printable PDF.

`vtf twin guide out/twins --selection docs/twins/selection.json` writes both from one source:
the setup, the three phone spots (vtf.twin.digital.PHOTO_SPOTS, the cameras the digital twins are
rendered from), one checklist per layout in sheet order (L01 = page 2 of layouts.pdf) with every
object's spot, and where the photos go. Written for someone working by voice: short numbered
steps, plain words.
"""

from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas

from vtf.core.io import read_scene
from vtf.core.layout import RunLayout
from vtf.twin.digital import PHOTO_SPOTS, PhotoSpot, layout_label
from vtf.twin.layouts import object_placements
from vtf.twin.mat import GRID_MM, PT

INBOX = "twins/inbox"
MD_FILE = "photo_session.md"
PDF_FILE = "photo_session.pdf"
PAGE_W, PAGE_H = (v / mm for v in A4)
MARGIN = 16.0


@dataclass(frozen=True)
class LayoutCard:
    label: str
    scene_id: str
    sheet_page: int
    objects: list[tuple[int, str, str, str]]  # number, name, "(x, y) cm", note


def _cm(v: float) -> str:
    return f"{v:+.1f}".replace("+0.0", "0").replace("-0.0", "0")


def layout_cards(run: Path, scene_ids: list[str]) -> list[LayoutCard]:
    lay = RunLayout(run)
    cards = []
    for i, sid in enumerate(scene_ids):
        scene = read_scene(lay.scene_file(sid))
        objs = []
        for p in object_placements(scene):
            note = "in the bowl" if p.obj.placement.kind == "in" else ""
            objs.append((p.number, p.obj.name, f"({_cm(p.centre_cm[0])}, {_cm(p.centre_cm[1])})",
                         note))  # fmt: skip
        cards.append(LayoutCard(layout_label(i), sid, i + 2, objs))
    return cards


def spot_lines(s: PhotoSpot) -> list[str]:
    """Plain words for one phone spot."""
    x, y = s.ground_cm()
    h = s.height_cm()
    front_gap = -y - GRID_MM[1] / 20  # cm in front of the mat's front edge
    if s.id == "front":
        return [
            f"Phone straight in front of the mat, in line with its centre, about {front_gap:.0f} "
            "cm in front of the mat's front edge.",
            f"Camera lens about {h:.0f} cm above the table (a box or a stack of books works).",
            f"Tilt the phone down about {s.elevation_deg:.0f} degrees, so the middle of the mat is "
            "in the middle of the screen.",
        ]
    if s.id == "overhead":
        return [
            "Phone flat, camera pointing straight down, right above the middle of the mat.",
            f"Camera lens about {h:.0f} cm above the table. Hold it, or rest it on something that "
            "spans the table.",
            "Top of the phone toward the mat's back edge (the edge with the title).",
        ]
    return [
        f"Phone at the mat's front-right corner: about {x:.0f} cm to the right of the centre line "
        f"and {front_gap:.0f} cm in front of the front edge.",
        f"Camera lens about {h:.0f} cm above the table.",
        f"Point it at the middle of the mat, tilted down about {s.elevation_deg:.0f} degrees.",
    ]


SETUP = (
    "Print docs/twins/mat_A3.pdf at 100% (actual size, no 'fit to page'). Check that both 10 cm "
    "bars on the mat measure exactly 10 cm with a ruler.",
    "Tape the mat flat on a plain table, front edge toward you (the edge that says FRONT EDGE). "
    "Tape along the paper's edges so it does not curl, never over the black corner squares.",
    "Light: daylight or ordinary room light. No desk lamp pointing at the mat, no flash. Do not "
    "let your own shadow fall on the mat.",
    "Phone camera: the normal 1x lens, no zoom, no portrait mode, no filters, held sideways "
    "(landscape). If it is an iPhone, set Settings, Camera, Formats to Most Compatible so photos "
    "are JPEG (HEIC also works on this Mac).",
    "Lay out the eight items from the shopping list and the layout sheets (docs/twins/"
    "layouts.pdf): one page per layout, page 2 is L01.",
)

RULES = (
    "All four black corner squares must be fully in every photo, uncovered and sharp.",
    "Close is good enough: the phone does not have to be exactly on its spot. The computer works "
    "out where the phone was from the corner squares, and renders the digital twin from there.",
    "Objects must be where the sheet says, within about a centimetre: the cross on the sheet is "
    "the middle of the object, the red arrow is the way its front faces. Round fruit can face any "
    "way.",
    "Nothing else on the mat: no phone, hands, cables or labels.",
)

PER_LAYOUT = (
    "Clear the mat.",
    "Open the layout's sheet page. Put the bowl down first if the layout has one, then every other "
    "object with its middle on its spot. Anything marked 'in the bowl' just goes into the bowl.",
    "Take three photos: front, overhead, side (the spots above). One photo each is enough; retake "
    "one if it is blurred.",
    "Tick the layout off and go to the next one.",
)

AFTER = (
    f"Copy all the photos to the Mac into the folder {INBOX}/ inside the project folder (AirDrop "
    "to Downloads, then move them). Names do not matter: the computer recognises which layout and "
    "which spot each photo shows. If you like, name them L01_front.jpg, L01_overhead.jpg, "
    "L01_side.jpg and so on.",
    "Tell Claude the photos are in. It checks every photo (gate G8: markers found, phone position "
    "worked out, every coloured object within 2 cm of its spot; white ones are checked by eye), "
    "renders each photo's digital twin from the same spot, and lists any photo to retake.",
)


def write_markdown(path: Path, cards: list[LayoutCard]) -> Path:
    lines = [
        "# Physical twins: photo session",
        "",
        f"{len(cards)} layouts, three photos each, about {len(cards) * 5} minutes. You need the "
        "printed mat, the eight shopping-list items, the layout sheets (layouts.pdf), a phone, a "
        "tape measure and a box or a stack of books. A printable copy of this guide is "
        f"[{PDF_FILE}]({PDF_FILE}).",
        "",
        "## Once, before you start",
        "",
    ]
    lines += [f"{i}. {s}" for i, s in enumerate(SETUP, 1)]
    lines += ["", "## The three phone spots", "",
              "The same three spots for every layout. Measure heights from the table top to the "
              "camera lens.", ""]  # fmt: skip
    for n, s in enumerate(PHOTO_SPOTS, 1):
        lines.append(f"{n}. **{s.id.capitalize()} photo** ({s.how}).")
        lines += [f"   - {t}" for t in spot_lines(s)]
    lines += ["", "## Rules for every photo", ""]
    lines += [f"{i}. {s}" for i, s in enumerate(RULES, 1)]
    lines += ["", "## For each layout", ""]
    lines += [f"{i}. {s}" for i, s in enumerate(PER_LAYOUT, 1)]
    lines += ["", "## The layouts", "",
              "Positions are in cm from the middle of the mat: x to the right, y toward the back "
              "edge, as printed on the mat's grid.", ""]  # fmt: skip
    for c in cards:
        lines.append(f"### {c.label}: sheet page {c.sheet_page} ({c.scene_id})")
        lines.append("")
        for num, name, where, note in c.objects:
            lines.append(f"{num}. {name} at {where}" + (f", {note}" if note else ""))
        lines.append("")
        names = ", ".join(f"{c.label}_{s.id}.jpg" for s in PHOTO_SPOTS)
        lines.append(f"Photos: front, overhead, side (names optional; if you name them: {names}).")
        lines.append("")
    lines += ["## After the last layout", ""]
    lines += [f"{i}. {s}" for i, s in enumerate(AFTER, 1)]
    lines += ["", "## Layout to scene file", "",
              "| Layout | Sheet page | Scene file |", "| --- | --- | --- |"]  # fmt: skip
    lines += [f"| {c.label} | {c.sheet_page} | out/twins/scenes/{c.scene_id}/scene.json |"
              for c in cards]  # fmt: skip
    lines.append("")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return path


# PDF ------------------------------------------------------------------------------------------


class _Pdf:
    def __init__(self, path: Path) -> None:
        self.c = rl_canvas.Canvas(str(path), pagesize=A4, invariant=1)
        self.c.setTitle("VTF physical twins: photo session")
        self.c.setAuthor("Visual Task Factory (vtf twin)")
        self.y = 0.0
        self._start()

    def _start(self) -> None:
        self.c.saveState()
        self.c.scale(mm, mm)
        self.y = PAGE_H - MARGIN

    def page(self) -> None:
        self.c.restoreState()
        self.c.showPage()
        self._start()

    def need(self, h: float) -> None:
        if self.y - h < MARGIN:
            self.page()

    def font(self, size: float, bold: bool = False) -> None:
        self.c.setFont("Helvetica-Bold" if bold else "Helvetica", size * PT)

    def wrap(self, text: str, width: float, size: float, bold: bool = False) -> list[str]:
        font = "Helvetica-Bold" if bold else "Helvetica"
        words, out, line = text.split(), [], ""
        for w in words:
            trial = f"{line} {w}".strip()
            if self.c.stringWidth(trial, font, size * PT) > width and line:
                out.append(line)
                line = w
            else:
                line = trial
        return out + ([line] if line else [])

    def heading(self, text: str, size: float = 14) -> None:
        self.need(14)
        self.y -= size * 0.5
        self.font(size, True)
        self.c.drawString(MARGIN, self.y, text)
        self.y -= 4

    def para(self, text: str, size: float = 10.5, indent: float = 0.0, bullet: str = "") -> None:
        width = PAGE_W - 2 * MARGIN - indent - (7 if bullet else 0)
        lines = self.wrap(text, width, size)
        lead = size * 0.48
        self.need(lead * len(lines) + 2)
        self.font(size)
        for k, line in enumerate(lines):
            self.y -= lead
            if bullet and k == 0:
                self.c.drawString(MARGIN + indent, self.y, bullet)
            self.c.drawString(MARGIN + indent + (7 if bullet else 0), self.y, line)
        self.y -= 1.6

    def steps(self, items: tuple[str, ...] | list[str], size: float = 10.5) -> None:
        for i, s in enumerate(items, 1):
            self.para(s, size, bullet=f"{i}.")

    def checkbox(self, x: float, y: float) -> None:
        self.c.setLineWidth(0.6 * PT)
        self.c.rect(x, y - 0.6, 3.6, 3.6, stroke=1, fill=0)


def _spot_diagram(pdf: _Pdf) -> None:
    """Top view of the mat with the three phone spots, and a side view with heights."""
    c = pdf.c
    pdf.need(78)
    top = pdf.y - 4
    cx, cy = MARGIN + 55, top - 38
    k = 1.0  # page mm per cm
    hw, hh = GRID_MM[0] / 20 * k, GRID_MM[1] / 20 * k
    c.setStrokeGray(0)
    c.setLineWidth(0.8 * PT)
    c.rect(cx - hw, cy - hh, 2 * hw, 2 * hh, stroke=1, fill=0)
    pdf.font(7)
    c.drawCentredString(cx, cy + hh + 1.5, "back edge")
    c.drawString(cx + 2, cy - hh - 3.5, "front edge")
    for sx, sy in ((-1, 1), (1, 1), (1, -1), (-1, -1)):
        c.rect(cx + sx * 18 * k - 1.5, cy + sy * 12 * k - 1.5, 3, 3, stroke=0, fill=1)
    c.setFillColorRGB(0.1, 0.45, 0.2)
    c.setStrokeColorRGB(0.1, 0.45, 0.2)
    for spot in PHOTO_SPOTS:
        x, y = spot.ground_cm()
        u, v = cx + x * k, cy + y * k
        c.circle(u, v, 1.6, stroke=0, fill=1)
        if spot.id != "overhead":
            c.setDash(1.5, 1)
            c.line(u, v, cx, cy)
            c.setDash()
        label = f"{spot.id} ({spot.height_cm():.0f} cm up)"
        if spot.id == "overhead":
            c.drawString(u + 2.5, v + 2.5, label)
        elif spot.id == "front":
            c.drawCentredString(u, v - 4, label)
        else:
            c.drawString(u + 2.5, v - 1, label)
    c.setFillGray(0)
    pdf.font(8, True)
    c.drawString(MARGIN, top, "Seen from above (1 mm = 1 cm)")
    # Side view (from the right): heights and tilt; the mat's front edge faces left.
    pdf.font(8, True)
    ox, oy, ks = MARGIN + 150, top - 66, 0.9  # mat centre on the page; page mm per cm
    c.drawString(ox - 55, top, "Seen from the right-hand side")
    c.setLineWidth(0.8 * PT)
    c.setStrokeGray(0)
    c.line(ox - 50, oy, ox + 20, oy)
    pdf.font(7)
    half = GRID_MM[1] / 20 * ks
    c.setLineWidth(2.2 * PT)
    c.line(ox - half, oy, ox + half, oy)  # the mat, 28 cm deep
    c.drawCentredString(ox, oy - 3.5, "mat")
    c.setLineWidth(0.6 * PT)
    for spot in PHOTO_SPOTS:
        if spot.id == "side":
            continue
        _, y = spot.ground_cm()
        u, v = ox + y * ks, oy + spot.height_cm() * ks
        c.setFillColorRGB(0.1, 0.45, 0.2)
        c.circle(u, v, 1.4, stroke=0, fill=1)
        c.setDash(1.5, 1)
        c.setStrokeColorRGB(0.1, 0.45, 0.2)
        c.line(u, v, ox, oy)
        c.setDash()
        c.setStrokeGray(0.5)
        c.line(u, oy, u, v)
        c.setFillGray(0)
        c.drawString(u + 2, v + 1, f"{spot.id}: lens {spot.height_cm():.0f} cm up")
        if spot.id == "front":
            gap = -y - GRID_MM[1] / 20
            c.drawCentredString((u + ox - half) / 2, oy + 1.5, f"{gap:.0f} cm")
    pdf.y = top - 74


def write_pdf(path: Path, cards: list[LayoutCard]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = _Pdf(path)
    pdf.heading("Physical twins: photo session", 18)
    pdf.para(
        f"{len(cards)} layouts, three photos each, about {len(cards) * 5} minutes. You need the "
        "printed mat, the eight shopping-list items, the layout sheets (layouts.pdf), a phone, a "
        "tape measure and a box or a stack of books."
    )
    pdf.heading("Once, before you start", 12)
    pdf.steps(SETUP)
    pdf.heading("The three phone spots (the same for every layout)", 12)
    pdf.para("Measure heights from the table top to the camera lens.", 9.5)
    for n, s in enumerate(PHOTO_SPOTS, 1):
        pdf.para(f"{s.id.capitalize()} photo: {s.how}.", 10.5, bullet=f"{n}.")
        for line in spot_lines(s):
            pdf.para(line, 9.5, indent=7, bullet="-")
    _spot_diagram(pdf)
    pdf.heading("Rules for every photo", 12)
    pdf.steps(RULES)
    pdf.heading("For each layout", 12)
    pdf.steps(PER_LAYOUT)
    pdf.page()
    pdf.heading("The layouts (tick each photo as you take it)", 14)
    pdf.para("Positions in cm from the middle of the mat: x to the right, y toward the back edge, "
             "as printed on the grid.", 9.5)  # fmt: skip
    for card in cards:
        pdf.need(14 + 5 * len(card.objects))
        pdf.y -= 5
        pdf.font(11.5, True)
        pdf.c.drawString(MARGIN, pdf.y, f"{card.label}   sheet page {card.sheet_page}")
        pdf.font(8)
        pdf.c.setFillGray(0.4)
        pdf.c.drawString(MARGIN + 45, pdf.y, card.scene_id)
        pdf.c.setFillGray(0)
        x = MARGIN + 100
        pdf.font(9)
        for spot in PHOTO_SPOTS:
            pdf.checkbox(x, pdf.y)
            pdf.c.drawString(x + 5, pdf.y, spot.id)
            x += 25
        pdf.y -= 1.5
        for num, name, where, note in card.objects:
            pdf.y -= 4.6
            pdf.font(10)
            pdf.c.drawString(MARGIN + 4, pdf.y, f"{num}.  {name}")
            pdf.c.drawString(MARGIN + 50, pdf.y, where)
            if note:
                pdf.font(9)
                pdf.c.drawString(MARGIN + 80, pdf.y, note)
        pdf.y -= 2
        pdf.c.setStrokeGray(0.8)
        pdf.c.line(MARGIN, pdf.y, PAGE_W - MARGIN, pdf.y)
        pdf.c.setStrokeGray(0)
    pdf.heading("After the last layout", 12)
    pdf.steps(AFTER)
    pdf.c.restoreState()
    pdf.c.showPage()
    pdf.c.save()
    return path


def make_guide(run: Path, selection: dict, out_dir: Path) -> list[Path]:
    cards = layout_cards(run, list(selection["scene_ids"]))
    out_dir = Path(out_dir)
    md = write_markdown(out_dir / MD_FILE, cards)
    pdf = write_pdf(out_dir / PDF_FILE, cards)
    print(f"vtf twin guide: {len(cards)} layouts -> {md}, {pdf} ({pdf.stat().st_size:,} bytes)")
    return [md, pdf]
