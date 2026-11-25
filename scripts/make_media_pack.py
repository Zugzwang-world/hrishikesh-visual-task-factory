# ruff: noqa: E501  (slide and caption copy is prose)
"""Slide-resolution stills for the demo pack (brief VTF-09, section 4), from frozen v1 only.

    uv run python scripts/make_media_pack.py --run out/v1

Writes into docs/media/pack/:
  hero_triptych.png      one v1 scene from the overhead, front and gripper cameras (1920 wide)
  camera_dial_strip.png  the same front render through the four camera-quality presets
  gate_funnel.png        the v1 funnel from candidate questions to shipped tasks (G1 to G7)

No Blender and no new scenes: the renders are v1's, the dial is vtf.camera (seed 0), the funnel
numbers come from docs/reports/v1/FROZEN.json and validate_report.json via docs/VALIDATION.md.
The turntables, gallery screenshots and the twin pair are recorded by scripts/record_screens.py.
"""

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from vtf.camera.dial import apply_to_beauty
from vtf.camera.presets import PRESETS
from vtf.core.io import read_manifest, read_rgb_png
from vtf.core.layout import RENDER_MANIFEST

OUT = Path("docs/media/pack")
SCENE = "scene_0103"  # the conflict-gallery trap: "nearest to the gripper's tip", truth jug, 2D box
FONT = "/System/Library/Fonts/Avenir Next.ttc"
DEMI, MEDIUM, REGULAR = 2, 5, 7
INK = (15, 16, 22)
GREY = (90, 94, 102)
BLUE = (23, 110, 184)  # template bar start #176EB8
ORANGE = (220, 131, 53)  # template bar end #DC8335
W = 1920


def font(size: int, face: int = MEDIUM) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT, size, index=face)
    except OSError:  # not on macOS
        return ImageFont.load_default(size)


def labelled_strip(tiles: list[tuple[str, np.ndarray]], out: Path, gap: int = 12) -> None:
    n = len(tiles)
    tw = (W - gap * (n - 1)) // n
    th = round(tiles[0][1].shape[0] * tw / tiles[0][1].shape[1])
    band = 64
    sheet = Image.new("RGB", (W, th + band), "white")
    d = ImageDraw.Draw(sheet)
    for i, (label, rgb) in enumerate(tiles):
        x = i * (tw + gap)
        sheet.paste(Image.fromarray(rgb).resize((tw, th), Image.LANCZOS), (x, band))
        d.text((x + 2, 10), label, font=font(34, DEMI), fill=INK)
    sheet.save(out, optimize=True)
    print(f"{out}: {sheet.size[0]}x{sheet.size[1]}")


def triptych(run: Path) -> None:
    rdir = run / "scenes" / SCENE / "render"
    m = read_manifest(rdir / RENDER_MANIFEST)
    tiles = []
    for cam_id, label in (("overhead", "Overhead"), ("front", "Front"), ("gripper", "Gripper")):
        cam = m.camera(cam_id)
        level = (cam.quality or {}).get("level")
        tag = f"{label} ({level.upper() if level == 'cctv' else level} camera)" if level else label
        tiles.append((tag, read_rgb_png(rdir / cam.beauty)))
    labelled_strip(tiles, OUT / "hero_triptych.png")


def dial_strip(run: Path) -> None:
    rdir = run / "scenes" / SCENE / "render"
    cam = read_manifest(rdir / RENDER_MANIFEST).camera("front")
    rgb = read_rgb_png(rdir / (cam.beauty_raw or cam.beauty))
    k = np.asarray(cam.K)
    names = {"clean": "Clean", "mild": "Mild (webcam)", "harsh": "Harsh (cheap, dark)",
             "cctv": "CCTV"}  # fmt: skip
    tiles = [(names.get(level, level), apply_to_beauty(rgb, make(0), k))
             for level, make in PRESETS.items()]  # fmt: skip
    labelled_strip(tiles, OUT / "camera_dial_strip.png")


# docs/VALIDATION.md, "The v1 funnel" (FROZEN.json and validate_report.json)
FUNNEL = [
    ("7,013", 7013, "candidate questions", "261 scenes x 3 cameras"),
    ("3,653", 3653, "reached the gates", "3,360 dropped while compiling"),
    ("3,475", 3475, "passed G1 to G4, G7", "178 rejected, 167 of them by G2"),
    ("3,959", 3959, "with G6 twins", "+484 twins whose answer changed"),
    ("3,181", 3181, "shipped (frozen v1)", "778 dropped by G5 balance"),
]


def funnel() -> None:
    w, h = 1800, 1160
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    top, row = 24, 226
    bar_x, bar_w, bar_h = 24, 860, 150
    top_n = FUNNEL[0][1]
    for i, (num, n, what, why) in enumerate(FUNNEL):
        y = top + i * row
        bw = max(60, round(bar_w * n / top_n))
        colour = ORANGE if "twins" in what else BLUE
        x0 = bar_x + (bar_w - bw) // 2
        d.rounded_rectangle((x0, y, x0 + bw, y + bar_h), radius=16, fill=colour)
        tw = d.textlength(num, font=font(76, DEMI))
        d.text((x0 + (bw - tw) / 2, y + 26), num, font=font(76, DEMI), fill="white")
        d.text((bar_x + bar_w + 44, y + 12), what, font=font(58, DEMI), fill=INK)
        d.text((bar_x + bar_w + 44, y + 84), why, font=font(44, REGULAR), fill=GREY)
    img.save(OUT / "gate_funnel.png", optimize=True)
    print(f"{OUT / 'gate_funnel.png'}: {w}x{h}")


TRUTH_RGB = (42, 120, 214)  # the Explorer's "true answer" blue
SHORTCUT_RGB = (235, 104, 52)  # the Explorer's "2D shortcut" orange


def stakes(run: Path) -> None:
    """The trap task t08138724d906efd6: front camera, "Which object is nearest to the gripper's tip?"
    Truth jug (3D nearest), 2D shortcut box; every Claude setting answered box. Outlines come from
    the rendered object-index mask, labels as in the Explorer (colour plus text)."""
    import cv2

    rdir = run / "scenes" / SCENE / "render"
    rgb = read_rgb_png(rdir / "front" / "beauty.png").copy()
    index = np.array(Image.open(rdir / "front" / "index.png"))
    marks = [
        (16, TRUTH_RGB, "jug: nearest in 3D (truth)"),
        (19, SHORTCUT_RGB, "box: looks nearest in 2D"),
    ]
    boxes = []
    for pass_index, colour, _ in marks:
        mask = (index == pass_index).astype(np.uint8)
        mask = cv2.dilate(mask, np.ones((7, 7), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(rgb, contours, -1, colour, 4, cv2.LINE_AA)
        boxes.append(cv2.boundingRect(np.vstack(contours)))
    img = Image.fromarray(rgb)
    d = ImageDraw.Draw(img)
    f = font(26, DEMI)
    for (x, y, w, h), (_, colour, label) in zip(boxes, marks, strict=True):
        tw = d.textlength(label, font=f)
        lx = min(max(4, x + w / 2 - tw / 2 - 10), img.width - tw - 24)
        ly = y + h + 8 if y + h + 48 < img.height else y - 44
        d.rounded_rectangle((lx, ly, lx + tw + 20, ly + 38), radius=8, fill=colour)
        d.text((lx + 10, ly + 4), label, font=f, fill="white")
    img.save(OUT / "stakes_front.jpg", quality=90)
    print(f"{OUT / 'stakes_front.jpg'}: {img.width}x{img.height}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="out/v1", help="the frozen v1 run folder (read only)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    run = Path(args.run)
    triptych(run)
    dial_strip(run)
    funnel()
    stakes(run)


if __name__ == "__main__":
    main()
