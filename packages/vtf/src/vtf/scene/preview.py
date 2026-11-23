"""Ray-cast previews of sampled scenes, without Blender (a sampler debugging aid).

Shades vtf.core.raycast output: flat object colours lit by normals estimated from the depth map,
object outlines from the index image, the printed grid and reach-zone outlines painted on table
pixels. Writes a strip of scenes (rows) by cameras (columns).

    uv run python -m vtf.scene.preview out/v1dev --scenes scene_0000 scene_0005 \
        --out docs/media/sampler_v1_raycast.jpg
"""

import argparse
from pathlib import Path

import cv2
import numpy as np

from vtf.core.io import read_scene
from vtf.core.layout import RunLayout
from vtf.core.projection import camera_to_world, intrinsics
from vtf.core.raycast import raycast
from vtf.core.scene import TABLE_PASS_INDEX, SceneSpec
from vtf.scene.sampler_v1 import zone_signed_distance

BACKGROUND = (0.75, 0.78, 0.82)


def _to_srgb8(linear: np.ndarray) -> np.ndarray:
    c = np.clip(linear, 0.0, 1.0)
    srgb = np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(c, 1 / 2.4) - 0.055)
    return (srgb * 255 + 0.5).astype(np.uint8)


def render_camera(scene: SceneSpec, camera_id: str, width: int = 512) -> np.ndarray:
    """(H, W, 3) uint8 RGB preview of one camera at `width` pixels wide."""
    cam = scene.camera(camera_id)
    w0, h0 = cam.resolution
    cam = cam.model_copy(update={"resolution": (width, round(h0 * width / w0))})
    small = scene.model_copy(update={"cameras": [cam]})
    rc = raycast(small, cam.id)
    h, w = rc.depth.shape

    # Camera-frame points from planar depth, then world points and normals.
    k = intrinsics(cam)
    uu, vv = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    z = rc.depth.astype(float)
    pts = np.stack([(uu - k[0, 2]) / k[0, 0] * z, (vv - k[1, 2]) / k[1, 1] * z, z], axis=-1)
    gx = np.gradient(pts, axis=1)
    gy = np.gradient(pts, axis=0)
    normal = np.cross(gx, gy)
    normal /= np.linalg.norm(normal, axis=-1, keepdims=True) + 1e-12
    normal = np.where(normal[..., 2:3] > 0, -normal, normal)  # face the camera
    light = np.array([-0.4, -0.6, -0.7])
    light /= np.linalg.norm(light)
    lambert = np.clip((normal * light).sum(-1), 0.0, 1.0)
    shade = 0.45 + 0.55 * lambert

    colour = rc.colour.copy()
    c2w = camera_to_world(cam)
    world = pts @ c2w[:3, :3].T + c2w[:3, 3]
    table = rc.index == TABLE_PASS_INDEX
    colour[table] = (0.42, 0.27, 0.15)
    if scene.table.grid_cell_m:
        cell = scene.table.grid_cell_m
        for step, strength in ((cell, 0.82), (10 * cell, 0.45)):
            fx = np.abs(world[..., 0] / step - np.round(world[..., 0] / step)) * step
            fy = np.abs(world[..., 1] / step - np.round(world[..., 1] / step)) * step
            line = table & ((fx < 0.0012) | (fy < 0.0012))
            colour[line] *= strength
    for zone in scene.zones:
        sd = np.vectorize(lambda x, y, zone=zone: zone_signed_distance(zone, x, y))(
            world[..., 0][table], world[..., 1][table]
        )
        band = np.zeros_like(table)
        band[table] = (sd <= 0) & (sd >= -zone.line_width_m)
        colour[band] = zone.rgb or (1.0, 1.0, 1.0)
    colour = colour * shade[..., None]
    colour[rc.index == 0] = BACKGROUND
    img = _to_srgb8(colour)

    idx = rc.index.astype(np.int32)
    edge = np.zeros_like(table)
    edge[:, 1:] |= idx[:, 1:] != idx[:, :-1]
    edge[1:, :] |= idx[1:, :] != idx[:-1, :]
    edge &= (idx > 0) & (idx != TABLE_PASS_INDEX)
    img[edge] = (img[edge] * 0.35).astype(np.uint8)

    d = scene.meta.difficulty
    label = f"{scene.meta.scene_id}  {camera_id}  quality {scene.camera(camera_id).quality.level}"
    if camera_id == scene.cameras[0].id and d:
        label += f"  | {d.get('n_objects')} objects, {d.get('lighting')} light"
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), _ = cv2.getTextSize(label, font, 0.42, 1)
    img[: th + 10, : tw + 12] = (img[: th + 10, : tw + 12] * 0.35).astype(np.uint8)
    cv2.putText(img, label, (6, th + 4), font, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
    return img


def preview_strip(scenes: list[SceneSpec], width: int = 512, gap: int = 4) -> np.ndarray:
    """Rows of scenes, columns of cameras (each scene's own camera order)."""
    rows = []
    for scene in scenes:
        tiles = [render_camera(scene, cam.id, width) for cam in scene.cameras]
        h = max(t.shape[0] for t in tiles)
        tiles = [np.pad(t, ((0, h - t.shape[0]), (0, 0), (0, 0)), constant_values=255)
                 for t in tiles]  # fmt: skip
        spacer = np.full((h, gap, 3), 255, np.uint8)
        row = tiles[0]
        for t in tiles[1:]:
            row = np.concatenate([row, spacer, t], axis=1)
        rows.append(row)
    w = max(r.shape[1] for r in rows)
    rows = [np.pad(r, ((0, gap), (0, w - r.shape[1]), (0, 0)), constant_values=255) for r in rows]
    return np.concatenate(rows, axis=0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", type=Path)
    ap.add_argument("--scenes", nargs="*", help="Scene ids (default: the first three).")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--width", type=int, default=512)
    ap.add_argument("--quality", type=int, default=85)
    args = ap.parse_args()
    layout = RunLayout(args.run)
    ids = args.scenes or layout.scene_ids()[:3]
    strip = preview_strip([read_scene(layout.scene_file(i)) for i in ids], args.width)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.out), cv2.cvtColor(strip, cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, args.quality])  # fmt: skip
    print(f"preview: {len(ids)} scenes -> {args.out} ({args.out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
