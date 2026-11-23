"""Render bridge: runs Blender headless on batches of scenes, enforces a per-scene timeout, applies
the camera-quality dial and records failures.

The core never imports bpy: it starts Blender as a subprocess on
packages/vtf-blender/scripts/render_scene.py with a job list (scene file, output folder) and
watches its output. The script prints `VTF_SCENE_START <n>` and `VTF_SCENE_DONE <n>` or
`VTF_SCENE_FAILED <n> <error>` per scene; a scene that runs longer than the timeout gets Blender
killed, is marked failed, and Blender restarts for the rest of the batch. After a scene is done
the bridge checks its manifest and settled scene and applies each camera's quality dial
(vtf.camera.apply), which rewrites the manifest last. A failed scene never keeps a manifest.
"""

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from vtf.camera.apply import apply_render_dir, dial_applied
from vtf.core.hashing import sha256_file
from vtf.core.io import read_manifest, read_scene
from vtf.core.layout import RENDER_MANIFEST, RunLayout

REPO_ROOT = Path(__file__).resolve().parents[5]
RENDER_SCRIPT = REPO_ROOT / "packages" / "vtf-blender" / "scripts" / "render_scene.py"
MAC_BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"
LOG_FILE = "render.log"
TMP_DIR = ".tmp"  # render_scene.py's scratch folder for EXRs
ERROR_MARKER = "VTF_RENDER_ERROR"
START_MARKER = "VTF_SCENE_START"
DONE_MARKER = "VTF_SCENE_DONE"
FAILED_MARKER = "VTF_SCENE_FAILED"
REPORT_VERSION = "0.2.0"
DEFAULT_TIMEOUT_S = 300.0
DEFAULT_BATCH_SIZE = 5
POLL_S = 0.25


@dataclass
class RenderResult:
    ok: bool
    seconds: float  # wall clock from the scene's start marker to the dial being applied
    manifest: Path | None
    error: str | None
    log: Path
    startup_seconds: float = 0.0  # Blender start-up before this scene (first scene of a batch)
    dial_seconds: float = 0.0
    timed_out: bool = False


@dataclass
class _Job:
    n: int  # position in the caller's list
    scene: Path
    out: Path
    extra: dict = field(default_factory=dict)  # extra job fields (test hooks)

    @property
    def log(self) -> Path:
        return self.out / LOG_FILE


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


def blender_command(blender: str, jobs_file: Path, device: str, gltf: bool = True) -> list[str]:
    cmd = [
        blender,
        "-b",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "-P",
        str(RENDER_SCRIPT),
        "--",
        "--jobs",
        str(jobs_file),
        "--device",
        device.upper(),
    ]
    if not gltf:
        cmd.append("--no-gltf")
    return cmd


def _last_error(lines: Sequence[str]) -> str:
    """The render script's VTF_RENDER_ERROR line, else the last line that mentions an error."""
    lines = [ln.strip() for ln in lines if ln.strip()]
    for line in reversed(lines):
        if line.startswith(ERROR_MARKER):
            return line.removeprefix(ERROR_MARKER).strip()[:500]
    for line in reversed(lines):
        if "Error" in line or "error" in line or "Exception" in line:
            return line[:500]
    return lines[-1][:500] if lines else "no output"


def _marker(line: str, marker: str) -> tuple[int, str] | None:
    """(n, rest) for a `<marker> <n> <rest>` line, else None."""
    if not line.startswith(marker + " "):
        return None
    parts = line.split(" ", 2)
    try:
        return int(parts[1]), (parts[2].strip() if len(parts) > 2 else "")
    except (IndexError, ValueError):
        return None


def _reader(stream, lines: queue.Queue) -> None:
    for line in iter(stream.readline, ""):
        lines.put(line)
    lines.put(None)


def _kill(proc: subprocess.Popen) -> None:
    proc.kill()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:  # pragma: no cover - a process that ignores SIGKILL
        pass


def _finish(job: _Job, scene: Path, keep_raw: bool) -> tuple[Path, float]:
    """Check the manifest and settled scene Blender wrote, then apply the camera dial."""
    manifest_path = job.out / RENDER_MANIFEST
    if not manifest_path.exists():
        raise RuntimeError(f"no {RENDER_MANIFEST} written")
    manifest = read_manifest(manifest_path)
    read_scene(job.out / manifest.settled_scene_file)
    _, dial_s = apply_render_dir(job.out, read_scene(scene), keep_raw=keep_raw)
    return manifest_path, dial_s


def _run_batch(
    blender: str,
    batch: list[_Job],
    device: str,
    gltf: bool,
    timeout: float,
    on_result: Callable[[_Job, RenderResult], None],
    keep_raw: bool = True,
) -> list[_Job]:
    """Run one Blender process on `batch`. Returns the jobs that never started (to retry)."""
    for job in batch:
        job.out.mkdir(parents=True, exist_ok=True)
        (job.out / RENDER_MANIFEST).unlink(missing_ok=True)  # never leave a stale manifest
    with tempfile.TemporaryDirectory(prefix="vtf_jobs_") as tmp:
        jobs_file = Path(tmp) / "jobs.json"
        jobs_file.write_text(
            json.dumps(
                [{"scene": str(j.scene), "out": str(j.out), **j.extra} for j in batch], indent=1
            ),
            encoding="utf-8",
        )
        launch = time.perf_counter()
        proc = subprocess.Popen(
            blender_command(blender, jobs_file, device, gltf),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            errors="replace",
        )
        lines: queue.Queue = queue.Queue()
        threading.Thread(target=_reader, args=(proc.stdout, lines), daemon=True).start()

        preamble: list[str] = []
        logs: dict[int, list[str]] = {}
        current: int | None = None  # index into batch of the running scene
        started_at = launch  # the first scene's clock includes start-up
        startup_s = 0.0
        finished: set[int] = set()

        def fail(i: int, error: str, timed_out: bool = False) -> None:
            job = batch[i]
            (job.out / RENDER_MANIFEST).unlink(missing_ok=True)
            shutil.rmtree(job.out / TMP_DIR, ignore_errors=True)  # EXRs of a killed render
            seconds = time.perf_counter() - started_at
            job.log.write_text("".join(logs.get(i, [])) + f"\n{error}\n", encoding="utf-8")
            finished.add(i)
            on_result(
                job,
                RenderResult(False, seconds, None, error, job.log, startup_s, 0.0, timed_out),
            )

        while True:
            # One clock: the running scene (the first includes start-up), or the gap after a
            # scene (Blender stuck between scenes or at exit; unstarted scenes are retried).
            if time.perf_counter() - started_at > timeout:
                _kill(proc)
                if current is not None:
                    fail(current, f"timed out after {timeout:g} s; Blender killed", True)
                elif not finished:
                    logs[0] = preamble
                    fail(0, f"Blender started no scene within {timeout:g} s; killed", True)
                current = None
                break
            try:
                line = lines.get(timeout=POLL_S)
            except queue.Empty:
                continue
            if line is None:  # Blender exited
                break
            text = line.rstrip("\n")
            if (m := _marker(text, START_MARKER)) is not None:
                current = m[0]
                now = time.perf_counter()
                if not finished and current == 0:
                    startup_s = now - launch
                    logs[current] = [*preamble, line]
                else:
                    started_at = now
                    startup_s = 0.0
                    logs[current] = [line]
                continue
            target = logs[current] if current is not None else preamble
            target.append(line)
            if (m := _marker(text, DONE_MARKER)) is not None and m[0] == current:
                job = batch[current]
                job.log.write_text("".join(logs[current]), encoding="utf-8")
                try:
                    manifest_path, dial_s = _finish(job, job.scene, keep_raw)
                except Exception as exc:
                    fail(current, f"{type(exc).__name__}: {exc}")
                else:
                    finished.add(current)
                    seconds = time.perf_counter() - started_at
                    on_result(
                        job,
                        RenderResult(
                            True, seconds, manifest_path, None, job.log, startup_s, dial_s
                        ),
                    )
                current = None
                started_at = time.perf_counter()
                startup_s = 0.0
            elif (m := _marker(text, FAILED_MARKER)) is not None and m[0] == current:
                fail(current, m[1] or _last_error(logs[current]))
                current = None
                started_at = time.perf_counter()
                startup_s = 0.0

        if proc.poll() is None:
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                _kill(proc)
        if current is not None:  # Blender died inside a scene
            fail(current, f"Blender exited {proc.returncode}: {_last_error(logs[current])}")
        if not finished:  # Blender died before any scene: fail the first so the run progresses
            logs[0] = preamble
            fail(0, f"Blender exited {proc.returncode}: {_last_error(preamble)}")
    return [job for i, job in enumerate(batch) if i not in finished]


def render_scenes(
    scene_files: Sequence[Path | str],
    out_dirs: Sequence[Path | str],
    device: str = "gpu",
    batch_size: int = DEFAULT_BATCH_SIZE,
    timeout_per_scene: float = DEFAULT_TIMEOUT_S,
    gltf: bool = True,
    blender: str | Path | None = None,
    on_result: Callable[[int, RenderResult], None] | None = None,
    job_extras: Sequence[dict] | None = None,
    keep_raw: bool = True,
) -> list[RenderResult]:
    """Render scene_files[i] into out_dirs[i], `batch_size` scenes per Blender process.

    A scene that takes longer than `timeout_per_scene` seconds (its first scene includes Blender
    start-up) is killed and marked failed, and Blender restarts for the rest. Every failure is
    recorded in the result, never raised, and leaves no manifest. `on_result(i, result)` is called
    as each scene finishes. `job_extras[i]` adds fields to scene i's job (test hooks).
    keep_raw False deletes beauty_raw.png after the dial (saves disk at scale).
    """
    if len(scene_files) != len(out_dirs):
        raise ValueError("scene_files and out_dirs differ in length")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    jobs = [
        _Job(i, Path(s).resolve(), Path(o).resolve(), dict(job_extras[i]) if job_extras else {})
        for i, (s, o) in enumerate(zip(scene_files, out_dirs, strict=True))
    ]
    results: list[RenderResult | None] = [None] * len(jobs)

    def record(job: _Job, result: RenderResult) -> None:
        results[job.n] = result
        if on_result is not None:
            on_result(job.n, result)

    try:
        blender_path = find_blender(blender)
    except FileNotFoundError as exc:
        for job in jobs:
            job.out.mkdir(parents=True, exist_ok=True)
            (job.out / RENDER_MANIFEST).unlink(missing_ok=True)
            job.log.write_text(f"{exc}\n", encoding="utf-8")
            record(job, RenderResult(False, 0.0, None, str(exc), job.log))
        return results  # type: ignore[return-value]

    pending = list(jobs)
    while pending:
        batch, pending = pending[:batch_size], pending[batch_size:]
        retry = _run_batch(blender_path, batch, device, gltf, timeout_per_scene, record, keep_raw)
        pending = retry + pending
    return results  # type: ignore[return-value]


def render_scene(
    scene_file: Path | str,
    out_dir: Path | str,
    device: str = "gpu",
    blender: str | Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    gltf: bool = True,
    keep_raw: bool = True,
) -> RenderResult:
    """Render one scene file into out_dir with Blender (a batch of one)."""
    return render_scenes(
        [scene_file], [out_dir], device, 1, timeout, gltf=gltf, blender=blender, keep_raw=keep_raw
    )[0]


def rendered_from(manifest_path: Path, scene_file: Path) -> bool:
    """True when a valid manifest exists, was rendered from this exact scene file (sha256) and
    carries the scene's camera-quality dial."""
    if not manifest_path.exists():
        return False
    try:
        manifest = read_manifest(manifest_path)
        if manifest.scene_sha256 != sha256_file(scene_file):
            return False
        return dial_applied(manifest, read_scene(scene_file))
    except Exception:  # unreadable or stale manifest: render again
        return False


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
    batch_size: int = DEFAULT_BATCH_SIZE,
    timeout_per_scene: float = DEFAULT_TIMEOUT_S,
    gltf: bool = True,
    keep_raw: bool = True,
) -> dict:
    """Render every <run>/scenes/<id>/scene.json into <run>/scenes/<id>/render/.

    Scenes whose manifest was rendered from the same scene file are skipped unless force;
    `limit` caps the number of scenes rendered (skips do not count), so repeated calls work
    through a run. Prints one line per scene and rewrites <run>/render_report.json after every
    scene, with per-scene status, seconds, phase timings and errors.
    """
    layout = RunLayout(run_dir)
    scene_ids = layout.scene_ids()
    if not scene_ids:
        raise FileNotFoundError(f"no scenes in {layout.scenes_dir}")
    blender_path = find_blender(blender)
    previous = _load_report(layout.render_report).get("scenes", {})
    report = {
        "report_version": REPORT_VERSION,
        "device": device.upper(),
        "blender": blender_path,
        "batch_size": batch_size,
        "timeout_per_scene_s": timeout_per_scene,
        "counts": {"ok": 0, "failed": 0, "skipped": 0},
        "seconds": 0.0,
        "scenes": dict(previous),
    }
    counts = report["counts"]
    start = time.perf_counter()
    todo: list[str] = []
    for scene_id in scene_ids:
        if limit is not None and len(todo) >= limit:
            break
        if not force and rendered_from(
            layout.render_manifest(scene_id), layout.scene_file(scene_id)
        ):
            counts["skipped"] += 1
            entry = dict(previous.get(scene_id, {}))
            entry.update(status="skipped", error=None)
            report["scenes"][scene_id] = entry
            print(f"{scene_id}  skipped (already rendered)", flush=True)
            continue
        todo.append(scene_id)

    def on_result(i: int, result: RenderResult) -> None:
        scene_id = todo[i]
        status = "ok" if result.ok else "failed"
        counts[status] += 1
        entry = {
            "status": status,
            "seconds": round(result.seconds, 3),
            "startup_seconds": round(result.startup_seconds, 3),
            "dial_seconds": round(result.dial_seconds, 3),
            "error": result.error,
            "log": str(result.log.relative_to(layout.root.resolve())),
        }
        if result.ok:
            manifest = read_manifest(result.manifest)
            entry["timings"] = manifest.timings
            entry["device"] = manifest.device
            entry["cameras"] = [c.camera_id for c in manifest.cameras]
            entry["settle"] = {
                "max_displacement_m": manifest.settle.max_displacement_m,
                "off_table": manifest.settle.off_table,
                "moving_at_end": manifest.settle.moving_at_end,
            }
            print(f"{scene_id}  ok  {result.seconds:.1f} s", flush=True)
        else:
            print(f"{scene_id}  FAILED  {result.seconds:.1f} s  {result.error}", flush=True)
        report["scenes"][scene_id] = entry
        report["seconds"] = round(time.perf_counter() - start, 3)
        _write_report(layout.render_report, report)

    if todo:
        render_scenes(
            [layout.scene_file(s) for s in todo],
            [layout.render_dir(s) for s in todo],
            device=device,
            batch_size=batch_size,
            timeout_per_scene=timeout_per_scene,
            gltf=gltf,
            blender=blender_path,
            on_result=on_result,
            keep_raw=keep_raw,
        )
    total = time.perf_counter() - start
    report["seconds"] = round(total, 3)
    _write_report(layout.render_report, report)
    rendered = counts["ok"] + counts["failed"]
    per_scene = f", {total / rendered:.1f} s per scene" if rendered else ""
    print(
        f"rendered {counts['ok']} ok, {counts['failed']} failed, {counts['skipped']} skipped "
        f"in {total:.1f} s{per_scene}; report {layout.render_report}",
        flush=True,
    )
    return report


def _write_report(path: Path, report: dict) -> None:
    data = dict(report, scenes=dict(sorted(report["scenes"].items())))
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
