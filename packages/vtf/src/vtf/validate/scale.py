"""Resumable scale run: generate, then render, validate, twin and collect in batches.

    uv run python scripts/scale_run.py --recipe recipes/first_scene.yaml --n 500 --seed 1 \\
        --out out/v1 --batch 25            # or: uv run vtf run ...

1. Generate N scenes from the recipe and seed, unless the run folder already holds them (same
   seed and recipe hash; any other run there is refused).
2. Loop over the scenes in batches: render the batch's scenes that are not rendered yet (one
   Blender batch, 5-minute timeout per scene), validate them (vtf.validate.pipeline, incremental),
   render and check G6 twins for a sample of the batch's new tasks, collect the run's manifest
   and report, then write <run>/logs/progress.json and one log line.

Resumable: a scene counts as rendered when its manifest exists and was made from this exact
scene file (the G7 check), so a restart never re-renders finished work; validation skips scenes
whose validation key is unchanged; twins resume from their pending G6 records. Scenes whose
render failed are not retried unless --retry-failed. A lock file stops two drivers sharing a run.
Logs go to <run>/logs/scale_run.log as well as stdout, so it is safe under nohup.

Disk floor: before each batch, if free space minus the projected size of the batch (measured
bytes per finished scene, twins and task folders included; 15 MB before any is measured) would
fall below --min-free-gb (default 20), the run stops cleanly at the last finished batch with
status stopped_disk_floor in progress.json and exits 0; a rerun resumes. Disk-lean output:
beauty_raw.png copies are deleted after each render unless --keep-beauty-raw; task images are
hard links.
"""

import atexit
import json
import os
import shutil
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

from vtf.core import layout
from vtf.core.io import read_model
from vtf.core.scene import RunInfo
from vtf.validate import pipeline as pl

DEFAULT_MIN_FREE_GB = 20.0
DEFAULT_SCENE_BYTES = 15e6  # per scene before any is measured (founder, brief 05)
GB = 1e9
LOCK_FILE = "scale_run.lock"
STATE_FILE = "scale_state.json"
PROGRESS_FILE = "progress.json"
LOG_FILE = "scale_run.log"
RENDER_TIMEOUT_S = 300.0


class RunLog:
    def __init__(self, logs_dir: Path) -> None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.path = logs_dir / LOG_FILE

    def __call__(self, message: str) -> None:
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{stamp}] {message}"
        print(line, flush=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_lock(logs_dir: Path) -> Path:
    lock = logs_dir / LOCK_FILE
    if lock.exists():
        try:
            pid = int(lock.read_text().strip() or 0)
        except ValueError:
            pid = 0
        if pid and pid != os.getpid() and _pid_alive(pid):
            raise SystemExit(f"another scale run (pid {pid}) holds {lock}")
    lock.write_text(f"{os.getpid()}\n")
    atexit.register(lambda: lock.unlink(missing_ok=True))
    return lock


def ensure_generated(recipe: Path, n: int, seed: int, lay: layout.RunLayout, log) -> None:
    from vtf.scene.generate import generate_run
    from vtf.scene.recipe import load_recipe

    expected = [layout.scene_id_for(i) for i in range(n)]
    if lay.run_file.exists():
        info = read_model(lay.run_file, RunInfo)
        recipe_hash = load_recipe(recipe).recipe_hash
        if info.run_seed != seed or info.recipe_hash != recipe_hash:
            raise SystemExit(
                f"{lay.root} holds a different run (seed {info.run_seed}, recipe "
                f"{info.recipe_hash[:8]}); use another --out"
            )
        if info.n >= n and all(lay.scene_file(s).exists() for s in expected):
            log(f"scenes: {n} already generated in {lay.root}")
            return
    log(f"scenes: generating {n} from {recipe} with seed {seed}")
    generate_run(recipe=recipe, n=n, seed=seed, out=lay.root)


def tree_bytes(paths: list[Path], seen: set | None = None) -> int:
    """Bytes on disk under these folders, each hard-linked file counted once."""
    seen = set() if seen is None else seen
    total = 0
    for root in paths:
        if not Path(root).exists():
            continue
        for dirpath, _, files in os.walk(root):
            for name in files:
                try:
                    st = os.lstat(os.path.join(dirpath, name))
                except FileNotFoundError:
                    continue
                key = (st.st_dev, st.st_ino)
                if key not in seen:
                    seen.add(key)
                    total += st.st_size
    return total


def disk_check(lay: layout.RunLayout, ids: list[str], n_next: int, min_free_gb: float) -> dict:
    """Would rendering the next n_next scenes leave less than min_free_gb free? The projection is
    the measured average bytes per finished scene (its render folder plus its share of twins and
    task folders), or DEFAULT_SCENE_BYTES before any scene is finished."""
    free = shutil.disk_usage(lay.root).free
    done = [s for s in ids if pl.is_rendered(lay.scene_file(s), lay.render_dir(s))]
    if done:
        used = tree_bytes([lay.scene_dir(s) for s in done] + [lay.twins_dir, lay.tasks_dir])
        per_scene, measured = used / len(done), True
    else:
        per_scene, measured = DEFAULT_SCENE_BYTES, False
    projected = per_scene * n_next
    return {
        "free_gb": round(free / GB, 2),
        "projected_gb": round(projected / GB, 3),
        "min_free_gb": min_free_gb,
        "mb_per_scene": round(per_scene / 1e6, 2),
        "measured": measured,
        "ok": free - projected >= min_free_gb * GB,
    }


def load_state(path: Path) -> dict:
    state = pl.read_json(path, {}) or {}
    state.setdefault("render", {})
    return state


def save_state(path: Path, state: dict) -> None:
    pl.atomic_write_text(path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def _fmt_seconds(s: float | None) -> str:
    if s is None:
        return "n/a"
    s = int(s)
    return f"{s // 3600}h{(s % 3600) // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


def progress(
    lay: layout.RunLayout,
    ids: list[str],
    state: dict,
    report: dict,
    started: float,
    work: dict,
    status: str = "running",
    disk: dict | None = None,
) -> dict:
    rendered = [s for s in ids if pl.is_rendered(lay.scene_file(s), lay.render_dir(s))]
    failed = [s for s in ids if state["render"].get(s, {}).get("status") == "failed"
              and s not in rendered]  # fmt: skip
    validated = [
        s for s in ids
        if (pl.scene_validate_dir(lay.scene_dir(s)) / pl.SCENE_REPORT).exists()
    ]  # fmt: skip
    elapsed = time.perf_counter() - started
    done_now = work["rendered"] + work["failed"]
    per_scene = elapsed / done_now if done_now else None
    remaining = len(ids) - len(rendered) - len(failed)
    eta = remaining * per_scene if per_scene is not None else (0.0 if remaining == 0 else None)
    render_s = work["render_seconds"] / work["rendered"] if work["rendered"] else None
    g6 = report.get("gates", {}).get("G6", {})
    return {
        "updated": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": status,
        "run": str(lay.root),
        "disk": disk,
        "scenes_total": len(ids),
        "scenes_rendered": len(rendered),
        "scenes_failed": len(failed),
        "failed_scenes": failed[:50],
        "scenes_validated": len(validated),
        "tasks_accepted": report.get("accepted", 0),
        "twin_tasks": report.get("twin_tasks", 0),
        "tasks_in_manifest": report.get("tasks_in_manifest", 0),
        "rejected": report.get("rejected", 0),
        "twins": {"sampled": g6.get("sampled", 0), **g6.get("statuses", {})},
        "gate_pass_rates": {g: s.get("pass_rate") for g, s in report.get("gates", {}).items()},
        "families": {f: c.get("accepted", 0) for f, c in report.get("families", {}).items()},
        "rigs": report.get("rigs", {}),
        "this_invocation": {
            "elapsed_seconds": round(elapsed, 1),
            "scenes_rendered": work["rendered"],
            "scenes_failed": work["failed"],
            "twins_rendered": work["twins"],
            "render_seconds_per_scene": None if render_s is None else round(render_s, 1),
            "wall_seconds_per_scene": None if per_scene is None else round(per_scene, 1),
        },
        "seconds_per_scene": None if per_scene is None else round(per_scene, 1),
        "eta_seconds": None if eta is None else round(eta),
        "eta": _fmt_seconds(eta),
        "batches_done": work["batches"],
    }


def scale_run(
    recipe: Path,
    n: int,
    seed: int,
    out: Path,
    batch: int = 25,
    twin_fraction: float = 0.2,
    twins: bool = True,
    max_scenes: int | None = None,
    device: str = "gpu",
    retry_failed: bool = False,
    gltf: bool = False,
    timeout: float = RENDER_TIMEOUT_S,
    min_free_gb: float = DEFAULT_MIN_FREE_GB,
    keep_beauty_raw: bool = False,
) -> dict:
    from vtf.validate.gates.g6_counterfactual_twin import run_g6
    from vtf.validate.render_batch import render_many, strip_beauty_raw

    lay = layout.RunLayout(out)
    lay.logs_dir.mkdir(parents=True, exist_ok=True)
    log = RunLog(lay.logs_dir)
    acquire_lock(lay.logs_dir)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(143)))
    started = time.perf_counter()
    twin_text = twin_fraction if twins else "off"
    log(
        f"scale run: {out} recipe={recipe} n={n} seed={seed} batch={batch} "
        f"twins={twin_text} device={device} min_free_gb={min_free_gb} pid={os.getpid()}"
    )
    ensure_generated(Path(recipe), n, seed, lay, log)
    run_info = read_model(lay.run_file, RunInfo)
    ids = [layout.scene_id_for(i) for i in range(n)]
    if max_scenes is not None:
        ids = ids[:max_scenes]
    state_path = lay.logs_dir / STATE_FILE
    state = load_state(state_path)
    work = {"rendered": 0, "failed": 0, "render_seconds": 0.0, "twins": 0, "batches": 0}
    batches = [ids[i : i + batch] for i in range(0, len(ids), batch)]
    report: dict = {}
    for b, chunk in enumerate(batches, 1):
        t0 = time.perf_counter()
        need = [
            s for s in chunk
            if not pl.is_rendered(lay.scene_file(s), lay.render_dir(s))
            and (retry_failed or state["render"].get(s, {}).get("status") != "failed")
        ]  # fmt: skip
        disk = disk_check(lay, ids, len(need), min_free_gb)
        if not disk["ok"]:
            report = pl.collect(lay.root, quiet=True)
            prog = progress(lay, ids, state, report, started, work, "stopped_disk_floor", disk)
            pl.atomic_write_text(lay.logs_dir / PROGRESS_FILE, json.dumps(prog, indent=2) + "\n")
            log(
                f"stopped_disk_floor before batch {b}/{len(batches)}: {disk['free_gb']} GB free, "
                f"next batch needs ~{disk['projected_gb']} GB ({disk['mb_per_scene']} MB per "
                f"scene), floor {min_free_gb} GB; {prog['scenes_rendered']}/{len(ids)} scenes "
                f"done, {prog['tasks_accepted']} tasks. Free space and rerun to resume."
            )
            return report
        if need:
            log(f"batch {b}/{len(batches)}: rendering {len(need)} scenes ({need[0]}..{need[-1]})")
            results = render_many(
                [lay.scene_file(s) for s in need], [lay.render_dir(s) for s in need],
                device=device, batch_size=batch, timeout=timeout, gltf=gltf, log=log,
                keep_beauty_raw=keep_beauty_raw,
            )  # fmt: skip
            for s, r in zip(need, results, strict=True):
                state["render"][s] = {"status": "ok" if r["ok"] else "failed",
                                      "seconds": r["seconds"], "error": r["error"]}  # fmt: skip
                work["rendered" if r["ok"] else "failed"] += 1
                work["render_seconds"] += r["seconds"] if r["ok"] else 0.0
            save_state(state_path, state)
        statuses: dict[str, int] = {}
        for s in chunk:
            if not keep_beauty_raw:
                strip_beauty_raw(lay.render_dir(s))  # idempotent; covers a crash after render
            try:
                st = pl.validate_scene(lay, s, run_info)
            except Exception as exc:  # one bad scene never stops the run; retried on rerun
                st = "error"
                state.setdefault("validate_errors", {})[s] = f"{type(exc).__name__}: {exc}"[:300]
                save_state(state_path, state)
                log(f"  validate {s} FAILED: {type(exc).__name__}: {exc}")
            statuses[st] = statuses.get(st, 0) + 1
        g6 = {}
        if twins and twin_fraction > 0:
            g6 = run_g6(lay, chunk, fraction=twin_fraction, device=device, batch_size=batch,
                        timeout=timeout, log=log, keep_beauty_raw=keep_beauty_raw)  # fmt: skip
            work["twins"] += sum(v for k, v in g6.items() if k in ("changed", "unchanged",
                                                                  "twin_invalid"))  # fmt: skip
        report = pl.collect(lay.root, quiet=True)
        work["batches"] += 1
        status = "finished" if b == len(batches) else "running"
        prog = progress(lay, ids, state, report, started, work, status,
                        disk_check(lay, ids, 0, min_free_gb))  # fmt: skip
        pl.atomic_write_text(lay.logs_dir / PROGRESS_FILE, json.dumps(prog, indent=2) + "\n")
        rates = ", ".join(
            f"{g} {r:.0%}" for g, r in prog["gate_pass_rates"].items() if r is not None
        )
        log(
            f"batch {b}/{len(batches)} done in {time.perf_counter() - t0:.0f} s: "
            f"rendered {len(need) - sum(1 for s in need if state['render'][s]['status'] != 'ok')}"
            f"/{len(need)} new, validate {statuses}, twins {g6 or 'none'}; run so far: "
            f"{prog['scenes_rendered']}/{prog['scenes_total']} scenes rendered, "
            f"{prog['scenes_failed']} failed, {prog['tasks_accepted']} tasks "
            f"(+{prog['twin_tasks']} twins); gates {rates}; ETA {prog['eta']}"
        )
    log(f"scale run finished in {_fmt_seconds(time.perf_counter() - started)}: "
        f"{report.get('accepted', 0)} tasks, {report.get('twin_tasks', 0)} twin tasks")  # fmt: skip
    return report
