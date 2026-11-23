"""Render bridge: runs Blender headless once per scene and records failures.

The core never imports bpy: it starts Blender as a subprocess on
packages/vtf-blender/scripts/render_scene.py, which reads the scene file and writes the
RenderManifest folder (vtf.core.render_manifest). The manifest is written last, so its presence
means the render finished.
"""

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from vtf.core.io import read_manifest, read_scene
from vtf.core.layout import RENDER_MANIFEST, RunLayout

REPO_ROOT = Path(__file__).resolve().parents[5]
RENDER_SCRIPT = REPO_ROOT / "packages" / "vtf-blender" / "scripts" / "render_scene.py"
MAC_BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"
LOG_FILE = "render.log"
ERROR_MARKER = "VTF_RENDER_ERROR"
REPORT_VERSION = "0.1.0"


@dataclass
class RenderResult:
    ok: bool
    seconds: float  # wall clock, including Blender start-up
    manifest: Path | None
    error: str | None
    log: Path


def _executable(candidate: str | Path) -> str | None:
    if Path(candidate).is_file():
        return str(candidate)
    return shutil.which(str(candidate))


def find_blender(blender: str | Path | None = None) -> str:
    """Blender executable: the argument, then $VTF_BLENDER, then the macOS app, then PATH."""
    for source, candidate in (
        ("argument", blender),
        ("$VTF_BLENDER", os.environ.get("VTF_BLENDER")),
    ):
        if candidate:
            found = _executable(candidate)
            if not found:
                raise FileNotFoundError(f"Blender not found at {candidate} (from {source})")
            return found
    found = _executable(MAC_BLENDER) or shutil.which("blender")
    if not found:
        raise FileNotFoundError(
            "Blender not found: pass --blender, set $VTF_BLENDER, or install " + MAC_BLENDER
        )
    return found


def blender_command(blender: str, scene_file: Path, out_dir: Path, device: str) -> list[str]:
    return [
        blender,
        "-b",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "-P",
        str(RENDER_SCRIPT),
        "--",
        "--scene",
        str(scene_file),
        "--out",
        str(out_dir),
        "--device",
        device.upper(),
    ]


def _last_error(log_text: str) -> str:
    """The render script's VTF_RENDER_ERROR line, else the last line that mentions an error."""
    lines = [ln.strip() for ln in log_text.splitlines() if ln.strip()]
    for line in reversed(lines):
        if line.startswith(ERROR_MARKER):
            return line.removeprefix(ERROR_MARKER).strip()[:500]
    for line in reversed(lines):
        if "Error" in line or "error" in line or "Exception" in line:
            return line[:500]
    return lines[-1][:500] if lines else "no output"


def render_scene(
    scene_file: Path | str,
    out_dir: Path | str,
    device: str = "gpu",
    blender: str | Path | None = None,
    timeout: float = 900,
) -> RenderResult:
    """Render one scene file into out_dir with Blender; validate the manifest and settled scene."""
    scene_file = Path(scene_file).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / LOG_FILE
    manifest_path = out_dir / RENDER_MANIFEST
    manifest_path.unlink(missing_ok=True)
    start = time.perf_counter()
    try:
        cmd = blender_command(find_blender(blender), scene_file, out_dir, device)
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout
        )
        log.write_text(proc.stdout, encoding="utf-8")
        if proc.returncode != 0:
            raise RuntimeError(f"Blender exited {proc.returncode}: {_last_error(proc.stdout)}")
        if not manifest_path.exists():
            raise RuntimeError(f"no {RENDER_MANIFEST} written: {_last_error(proc.stdout)}")
        manifest = read_manifest(manifest_path)
        read_scene(out_dir / manifest.settled_scene_file)
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        log.write_text(output, encoding="utf-8")
        return RenderResult(
            False, time.perf_counter() - start, None, f"timed out after {timeout:g} s", log
        )
    except Exception as exc:  # every failure is recorded in the result, not raised
        if not log.exists():
            log.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        return RenderResult(False, time.perf_counter() - start, None, str(exc), log)
    return RenderResult(True, time.perf_counter() - start, manifest_path, None, log)


def _load_report(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def render_run(
    run_dir: Path | str,
    device: str = "gpu",
    blender: str | Path | None = None,
    limit: int | None = None,
    force: bool = False,
) -> dict:
    """Render every <run>/scenes/<id>/scene.json into <run>/scenes/<id>/render/.

    Scenes that already have a manifest are skipped unless force. Prints one line per scene and
    writes <run>/render_report.json with per-scene status, seconds and errors.
    """
    layout = RunLayout(run_dir)
    scene_ids = layout.scene_ids()
    if not scene_ids:
        raise FileNotFoundError(f"no scenes in {layout.scenes_dir}")
    if limit is not None:
        scene_ids = scene_ids[:limit]
    blender_path = find_blender(blender)
    previous = _load_report(layout.render_report).get("scenes", {})
    scenes: dict[str, dict] = dict(previous)
    counts = {"ok": 0, "failed": 0, "skipped": 0}
    start = time.perf_counter()
    for scene_id in scene_ids:
        if layout.render_manifest(scene_id).exists() and not force:
            counts["skipped"] += 1
            entry = dict(previous.get(scene_id, {}))
            entry.update(status="skipped", error=None)
            scenes[scene_id] = entry
            print(f"{scene_id}  skipped (already rendered)", flush=True)
            continue
        result = render_scene(
            layout.scene_file(scene_id), layout.render_dir(scene_id), device, blender_path
        )
        status = "ok" if result.ok else "failed"
        counts[status] += 1
        entry = {
            "status": status,
            "seconds": round(result.seconds, 3),
            "error": result.error,
            "log": str(result.log.relative_to(layout.root.resolve())),
        }
        if result.ok:
            manifest = read_manifest(result.manifest)
            entry["timings"] = manifest.timings
            entry["device"] = manifest.device
            entry["settle"] = {
                "max_displacement_m": manifest.settle.max_displacement_m,
                "off_table": manifest.settle.off_table,
                "moving_at_end": manifest.settle.moving_at_end,
            }
            print(f"{scene_id}  ok  {result.seconds:.1f} s", flush=True)
        else:
            print(f"{scene_id}  FAILED  {result.seconds:.1f} s  {result.error}", flush=True)
        scenes[scene_id] = entry
    total = time.perf_counter() - start
    report = {
        "report_version": REPORT_VERSION,
        "device": device.upper(),
        "blender": blender_path,
        "counts": counts,
        "seconds": round(total, 3),
        "scenes": dict(sorted(scenes.items())),
    }
    path = layout.render_report
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    rendered = counts["ok"] + counts["failed"]
    per_scene = f", {total / rendered:.1f} s per scene" if rendered else ""
    print(
        f"rendered {counts['ok']} ok, {counts['failed']} failed, {counts['skipped']} skipped "
        f"in {total:.1f} s{per_scene}; report {path}",
        flush=True,
    )
    return report
