"""Apply each camera's quality dial to a finished render folder (called by the render bridge).

For every camera whose CameraQuality changes the image: beauty.png moves to beauty_raw.png and
the degraded image becomes beauty.png; with lens distortion, index.png, depth.npz and every amodal
mask are warped in place with the same nearest-neighbour map. The manifest records beauty_raw and
the applied CameraQuality, and is rewritten last.
"""

import time
from pathlib import Path

import numpy as np

from vtf.camera.dial import apply_to_beauty, is_clean, warp_nearest
from vtf.core.io import (
    read_depth,
    read_index_png,
    read_manifest,
    read_mask_png,
    read_rgb_png,
    read_scene,
    write_depth,
    write_index_png,
    write_mask_png,
    write_model,
    write_rgb_png,
)
from vtf.core.layout import RENDER_MANIFEST
from vtf.core.render_manifest import CameraOutputs, RenderManifest
from vtf.core.scene import CameraQuality, SceneSpec

BEAUTY_RAW = "beauty_raw.png"


def applied_quality(q: CameraQuality) -> dict:
    """What CameraOutputs.quality holds: empty for a clean camera, else the full CameraQuality."""
    return {} if is_clean(q) else q.model_dump(mode="json")


def dial_applied(manifest: RenderManifest, scene: SceneSpec) -> bool:
    """True when every camera's recorded quality matches the scene's dial (skip rule)."""
    try:
        return all(
            manifest.camera(cam.id).quality == applied_quality(cam.quality) for cam in scene.cameras
        )
    except KeyError:
        return False


def _apply_camera(
    out_dir: Path, cam: CameraOutputs, q: CameraQuality, keep_raw: bool
) -> CameraOutputs:
    k = np.asarray(cam.K, dtype=np.float64)
    beauty = out_dir / cam.beauty
    raw = beauty.with_name(BEAUTY_RAW)
    if not raw.exists():  # never degrade an already degraded image
        beauty.replace(raw)
    write_rgb_png(beauty, apply_to_beauty(read_rgb_png(raw), q, k))
    if q.distortion_k1 != 0:
        index_path = out_dir / cam.index
        write_index_png(index_path, warp_nearest(read_index_png(index_path), q, k, fill=0))
        depth_path = out_dir / cam.depth
        write_depth(depth_path, warp_nearest(read_depth(depth_path), q, k, fill=0.0))
        for rel in cam.amodal.values():
            write_mask_png(out_dir / rel, warp_nearest(read_mask_png(out_dir / rel), q, k, False))
    beauty_raw = str(Path(cam.beauty).with_name(BEAUTY_RAW).as_posix()) if keep_raw else None
    return cam.model_copy(update={"beauty_raw": beauty_raw, "quality": applied_quality(q)})


def apply_render_dir(
    out_dir: Path | str, scene: SceneSpec | None = None, keep_raw: bool = True
) -> tuple[RenderManifest, float]:
    """Apply the dial of every camera in `scene` (default: the settled scene) to out_dir.

    Returns the rewritten manifest and the seconds spent. Cameras already recorded as dialled
    with the same quality are left alone, so a repeated call is a no-op. With keep_raw False
    (saves disk at scale) beauty_raw.png is deleted once the manifest is rewritten and
    CameraOutputs.beauty_raw stays None; until then it exists, so a crash never leaves a beauty
    that could be degraded twice.
    """
    start = time.perf_counter()
    out_dir = Path(out_dir)
    manifest_path = out_dir / RENDER_MANIFEST
    manifest = read_manifest(manifest_path)
    if scene is None:
        scene = read_scene(out_dir / manifest.settled_scene_file)
    cameras, changed = [], False
    for cam_out in manifest.cameras:
        q = scene.camera(cam_out.camera_id).quality
        if is_clean(q) or cam_out.quality == applied_quality(q):
            cameras.append(cam_out)
            continue
        if cam_out.quality:
            raise RuntimeError(
                f"{out_dir}: camera {cam_out.camera_id} already carries a different dial; "
                "re-render it"
            )
        cameras.append(_apply_camera(out_dir, cam_out, q, keep_raw))
        changed = True
    if changed:
        manifest = manifest.model_copy(update={"cameras": cameras})
        write_model(manifest_path, manifest)
    if not keep_raw:
        for cam_out in manifest.cameras:
            if cam_out.beauty_raw is None:
                (out_dir / cam_out.beauty).with_name(BEAUTY_RAW).unlink(missing_ok=True)
    return manifest, time.perf_counter() - start
