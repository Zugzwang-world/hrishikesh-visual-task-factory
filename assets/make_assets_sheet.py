"""Tile the thumbnails from assets/blender_asset_check.py into one labelled contact sheet.

    uv run python assets/make_assets_sheet.py out/asset_check docs/media/assets_sheet.jpg

Each tile shows the asset settled on a 1 cm grid table, labelled with its id, category, colour
word and size in cm, and flagged when the Blender mesh size differs from the manifest by more
than 2 mm or the settle did not come to rest.
"""

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from vtf.core.assets import load_asset_manifest

COLS = 8
LABEL_H = 34


def font(size: int):
    for name in ("/System/Library/Fonts/Helvetica.ttc", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main(check_dir: Path, out: Path) -> None:
    results = json.loads((check_dir / "assets_check.json").read_text())
    manifest = load_asset_manifest()
    ids = [a.id for a in manifest.assets if a.id in results]
    first = Image.open(check_dir / results[ids[0]]["thumbnail"])
    w, h = first.size
    rows = (len(ids) + COLS - 1) // COLS
    sheet = Image.new("RGB", (COLS * w, rows * (h + LABEL_H)), (250, 250, 248))
    draw = ImageDraw.Draw(sheet)
    big, small = font(13), font(11)
    for i, asset_id in enumerate(ids):
        r = results[asset_id]
        entry = manifest.get(asset_id)
        x, y = (i % COLS) * w, (i // COLS) * (h + LABEL_H)
        sheet.paste(Image.open(check_dir / r["thumbnail"]).convert("RGB"), (x, y))
        size_ok = all(
            abs(a - b) <= 2e-3 for a, b in zip(r["blender_size_m"], entry.size_m, strict=True)
        )
        settle = r["settle"]
        rest_ok = not settle["moving"] and settle["displacement_m"] < 0.01
        cm = " x ".join(f"{v * 100:.0f}" for v in entry.size_m)
        name = " ".join(w for w in (entry.color_name, entry.category) if w)
        flag = "" if size_ok and rest_ok else "  CHECK"
        draw.text((x + 4, y + h + 2), f"{name}  {cm} cm{flag}", fill=(20, 20, 20), font=big)
        draw.text((x + 4, y + h + 18), asset_id, fill=(110, 110, 110), font=small)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=82, optimize=True)
    print(out, sheet.size, f"{out.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
