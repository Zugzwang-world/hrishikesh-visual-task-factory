"""Render many scene files through the render bridge, for G6 twins and the scale run.

Uses `vtf.render.bridge.render_scenes` (batched Blender, per-scene timeout) when the render lane
provides it, else `render_scene` one scene at a time. Success is judged from the files, not the
return value: a scene is rendered when its manifest exists and was made from this scene file.
"""

import time
from collections.abc import Callable, Sequence
from pathlib import Path

from vtf.validate.pipeline import is_rendered

DEFAULT_TIMEOUT_S = 300.0


def _item(results, i: int, n: int):
    if isinstance(results, list | tuple) and len(results) == n:
        return results[i]
    return None


def render_many(
    scene_files: Sequence[Path],
    out_dirs: Sequence[Path],
    device: str = "gpu",
    batch_size: int = 25,
    timeout: float = DEFAULT_TIMEOUT_S,
    gltf: bool = False,
    log: Callable[[str], None] = print,
) -> list[dict]:
    """Render each scene file into its out dir; one {ok, seconds, error} per scene, in order."""
    from vtf.render import bridge

    scene_files = [Path(p) for p in scene_files]
    out_dirs = [Path(p) for p in out_dirs]
    n = len(scene_files)
    if not n:
        return []
    out: list[dict] = []
    batched = getattr(bridge, "render_scenes", None)
    if batched is not None:
        t0 = time.perf_counter()
        results, error = None, None
        try:
            results = batched(scene_files, out_dirs, device=device, batch_size=batch_size,
                              timeout_per_scene=timeout, gltf=gltf)  # fmt: skip
        except TypeError:
            try:
                results = batched(scene_files, out_dirs, device=device, batch_size=batch_size)
            except Exception as exc:  # recorded per scene below
                error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # recorded per scene below
            error = f"{type(exc).__name__}: {exc}"
        per_scene = (time.perf_counter() - t0) / n
        for i, (sf, od) in enumerate(zip(scene_files, out_dirs, strict=True)):
            r = _item(results, i, n)
            ok = is_rendered(sf, od)
            out.append({
                "ok": ok,
                "seconds": round(float(getattr(r, "seconds", per_scene) or per_scene), 3),
                "error": None if ok else (getattr(r, "error", None) or error or "render failed"),
            })  # fmt: skip
        return out
    for sf, od in zip(scene_files, out_dirs, strict=True):
        r = bridge.render_scene(sf, od, device=device, timeout=timeout)
        ok = bool(r.ok) and is_rendered(sf, od)
        out.append({"ok": ok, "seconds": round(r.seconds, 3), "error": None if ok else r.error})
        log(f"  rendered {sf.parent.name}: {'ok' if ok else 'FAILED ' + str(r.error)} "
            f"in {r.seconds:.1f} s")  # fmt: skip
    return out
