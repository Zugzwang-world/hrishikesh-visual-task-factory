"""A 2x2 sample of the four camera-quality presets on one render (for docs and the demo).

    uv run python -m vtf.camera.sample <render_dir> [--camera front] [--out sample.jpg]

Reads <camera>/beauty_raw.png (or beauty.png when the camera was rendered clean) and K from the
render manifest, applies clean, mild, harsh and cctv with seed 0, and writes a labelled JPEG.
Labels are drawn on the sample sheet only, never on dataset images.
"""

from pathlib import Path

import cv2
import numpy as np
import typer

from vtf.camera.dial import apply_to_beauty
from vtf.camera.presets import PRESETS
from vtf.core.io import read_manifest, read_rgb_png
from vtf.core.layout import RENDER_MANIFEST

TILE_W = 640


def dial_sample(rgb: np.ndarray, k: np.ndarray, seed: int = 0) -> np.ndarray:
    """(2H', 2W', 3) uint8 RGB sheet: clean, mild / harsh, cctv, each tile TILE_W wide."""
    tiles = []
    for level, make in PRESETS.items():
        img = apply_to_beauty(rgb, make(seed), k)
        h = round(img.shape[0] * TILE_W / img.shape[1])
        tile = cv2.resize(img, (TILE_W, h), interpolation=cv2.INTER_AREA)
        cv2.rectangle(tile, (0, 0), (118, 34), (255, 255, 255), thickness=-1)
        cv2.putText(
            tile, level, (8, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (20, 20, 20), 2, cv2.LINE_AA
        )
        tiles.append(tile)
    return np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:])])


def main(
    render_dir: Path = typer.Argument(..., help="A render folder with render_manifest.json."),
    camera: str = typer.Option("front", help="Camera id."),
    out: Path = typer.Option(Path("out/camera_dial_sample.jpg"), help="Output JPEG."),
    quality: int = typer.Option(82, help="JPEG quality of the sheet."),
) -> None:
    manifest = read_manifest(render_dir / RENDER_MANIFEST)
    cam = manifest.camera(camera)
    beauty = render_dir / (cam.beauty_raw or cam.beauty)
    sheet = dial_sample(read_rgb_png(beauty), np.asarray(cam.K))
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(
        str(out), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality]
    )
    typer.echo(f"{out}: {sheet.shape[1]}x{sheet.shape[0]}, {out.stat().st_size} bytes")


if __name__ == "__main__":
    typer.run(main)
