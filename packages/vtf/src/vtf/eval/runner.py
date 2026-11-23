"""vtf eval: run one model on the accepted tasks of a run, score it, write results and a summary.

Reads <run>/manifest.jsonl in order. By default it takes the first `limit` tasks; with `sample` it
takes a seeded stratified sample (equal share per stratum, e.g. family x rig), and with
`task_ids` exactly those tasks. Cached replies are reused (no API call); misses go to the adapter
on a small thread pool. Writes <run>/eval/<model>/results.jsonl (one EvalRecord per task, manifest
order), summary.json and, for a sample, selection.json.

Blind mode (G5 question-only baseline) sends no image and files results under
eval/<model>__blind/ (see vtf.eval.blind).
"""

import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

from vtf.core.io import read_jsonl, read_task, write_jsonl, write_model
from vtf.core.layout import EVAL_RESULTS, EVAL_SUMMARY, RunLayout
from vtf.core.rng import derive_seed, make_rng
from vtf.core.task import EvalRecord, ManifestRow, TaskPackage
from vtf.eval.adapters import Adapter, AdapterResponse, FatalAdapterError, make_adapter
from vtf.eval.blind import blind_key, blind_prompt_version, make_blind_adapter
from vtf.eval.cache import EvalCache
from vtf.eval.config import resolve_model
from vtf.eval.prompt import PROMPT_VERSION
from vtf.eval.scorer import Score, score_response
from vtf.eval.summary import EvalSummary, conflict_label, format_summary, summarise

FINAL_STOPS = {None, "end_turn", "stop_sequence"}
SELECTION_FILE = "selection.json"
STRATIFY_KEYS = ("family", "rig", "camera_id", "shortcut_conflict", "answer_type")
DEFAULT_STRATIFY = ("family", "rig")


# --- task selection ---------------------------------------------------------------------------


def manifest_rows(run_dir: Path) -> list[ManifestRow]:
    layout = RunLayout(run_dir)
    if not layout.task_manifest.is_file():
        raise FileNotFoundError(f"{layout.task_manifest} not found: run vtf validate first")
    return [ManifestRow.model_validate(r) for r in read_jsonl(layout.task_manifest)]


def stratum_value(row: ManifestRow | TaskPackage, key: str) -> str:
    if key not in STRATIFY_KEYS:
        raise ValueError(f"cannot stratify by {key!r}; use any of {', '.join(STRATIFY_KEYS)}")
    value = getattr(row, key)
    return conflict_label(value) if key == "shortcut_conflict" else str(value)


def stratified_sample[R: ManifestRow | TaskPackage](
    rows: Sequence[R], n: int, keys: Iterable[str], seed: int
) -> list[R]:
    """A seeded sample of `n` rows with an equal share per stratum (strata smaller than their
    share give all their rows and the rest flows to the others). Rows come back in input order.

    Pure function of the row ids, the keys and the seed: the same manifest gives the same sample.
    """
    keys = tuple(keys)
    if n >= len(rows):
        return list(rows)
    strata: dict[tuple[str, ...], list[R]] = defaultdict(list)
    for row in rows:
        strata[tuple(stratum_value(row, k) for k in keys)].append(row)
    picked: set[str] = set()
    remaining = n
    order = sorted(strata, key=lambda s: (len(strata[s]), s))
    for i, stratum in enumerate(order):
        members = sorted(strata[stratum], key=lambda r: r.task_id)
        take = min(len(members), remaining // (len(order) - i))
        rng = make_rng(derive_seed(seed, "sample", *stratum))
        for j in rng.permutation(len(members))[:take]:
            picked.add(members[int(j)].task_id)
        remaining -= take
    return [row for row in rows if row.task_id in picked]


def per_group_sample[R: ManifestRow | TaskPackage](
    rows: Sequence[R], n_per_group: int, group: str, keys: Iterable[str], seed: int
) -> list[R]:
    """`n_per_group` rows from each value of `group` (e.g. family), stratified within it by
    `keys` (e.g. rig). Rows come back in input order."""
    keys = tuple(keys)
    groups: dict[str, list[R]] = defaultdict(list)
    for row in rows:
        groups[stratum_value(row, group)].append(row)
    picked = {
        row.task_id
        for name, members in groups.items()
        for row in stratified_sample(members, n_per_group, keys, derive_seed(seed, group, name))
    }
    return [row for row in rows if row.task_id in picked]


def select_rows(
    rows: list[ManifestRow],
    *,
    limit: int | None = None,
    sample: int | None = None,
    stratify: Iterable[str] = DEFAULT_STRATIFY,
    seed: int = 0,
    task_ids: Iterable[str] | None = None,
) -> list[ManifestRow]:
    if task_ids is not None:
        wanted = set(task_ids)
        missing = wanted - {r.task_id for r in rows}
        if missing:
            raise ValueError(f"{len(missing)} task id(s) not in the manifest, e.g. {min(missing)}")
        rows = [r for r in rows if r.task_id in wanted]
    if sample is not None:
        rows = stratified_sample(rows, sample, stratify, seed)
    if limit is not None:
        rows = rows[:limit]
    return rows


def load_tasks(
    run_dir: Path,
    limit: int | None = None,
    *,
    sample: int | None = None,
    stratify: Iterable[str] = DEFAULT_STRATIFY,
    seed: int = 0,
    task_ids: Iterable[str] | None = None,
) -> list[tuple[TaskPackage, Path]]:
    """Accepted tasks in manifest order (first `limit`, a stratified `sample`, or `task_ids`),
    each with its image path."""
    layout = RunLayout(run_dir)
    rows = select_rows(
        manifest_rows(run_dir),
        limit=limit,
        sample=sample,
        stratify=stratify,
        seed=seed,
        task_ids=task_ids,
    )
    tasks = []
    for row in rows:
        task_file = layout.root / row.path
        task = read_task(task_file)
        tasks.append((task, task_file.parent / task.image))
    return tasks


# --- scoring ----------------------------------------------------------------------------------


def score_task(task: TaskPackage, raw_text: str) -> Score:
    """Score a raw reply against a task. The one place the runner and the audit call the scorer."""
    return score_response(task.answer_type, task.answer, raw_text, task.tolerance)


def score_value_for_task(task: TaskPackage, value: Any) -> Score:
    """Score an already-extracted answer value (e.g. a human audit answer) the same way."""
    return score_task(task, json.dumps({"answer": value}))


def _record(
    task: TaskPackage,
    model: str,
    response: AdapterResponse,
    cached: bool,
    prompt_version: str = PROMPT_VERSION,
    with_image: bool = True,
) -> EvalRecord:
    score = score_task(task, response.raw_text)
    error = response.error
    if error is None and response.stop_reason not in FINAL_STOPS:
        error = f"stop_reason: {response.stop_reason}"
    return EvalRecord(
        task_id=task.task_id,
        model=model,
        prompt_version=prompt_version,
        family=task.family,
        camera_id=task.camera_id,
        rig=task.rig,
        shortcut_conflict=task.shortcut_conflict,
        with_image=with_image,
        answer_type=task.answer_type,
        truth=task.answer,
        raw_response=response.raw_text,
        parsed=score.parsed,
        parse_ok=score.parse_ok,
        correct=score.correct,
        cached=cached,
        latency_s=response.latency_s,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        error=error,
    )


def _ask(adapter: Adapter, task: TaskPackage, image: Path) -> AdapterResponse:
    if not image.is_file():
        return AdapterResponse(raw_text="", error=f"image not found: {image}")
    return adapter.answer(image, task.question, task.answer_type)


# --- the run ----------------------------------------------------------------------------------


def eval_run(
    run_dir: Path | str,
    model: str = "claude-sonnet-5-5",
    limit: int | None = None,
    models_config: Path | str | None = None,
    *,
    cache_path: Path | str | None = None,
    adapter: Adapter | None = None,
    concurrency: int | None = None,
    echo: Callable[[str], Any] = print,
    sample: int | None = None,
    stratify: Iterable[str] = DEFAULT_STRATIFY,
    seed: int = 0,
    task_ids: Iterable[str] | None = None,
    blind: bool = False,
) -> EvalSummary:
    """Evaluate `model` on accepted tasks of `run_dir`: the first `limit`, a seeded stratified
    `sample`, or exactly `task_ids`. `blind` sends the question without the image (G5)."""
    load_dotenv(find_dotenv(usecwd=True))
    run_dir = Path(run_dir)
    stratify = tuple(stratify)
    spec = resolve_model(model, models_config)
    tasks = load_tasks(
        run_dir, limit, sample=sample, stratify=stratify, seed=seed, task_ids=task_ids
    )
    key = blind_key(spec.key) if blind else spec.key
    prompt_version = blind_prompt_version() if blind else PROMPT_VERSION
    try:
        adapter = adapter or (make_blind_adapter(spec) if blind else make_adapter(spec))
    except FatalAdapterError as exc:
        raise SystemExit(f"vtf eval: {exc}") from exc

    responses: dict[str, tuple[AdapterResponse, bool]] = {}
    with EvalCache(cache_path) as cache:
        misses = []
        for task, image in tasks:
            hit = cache.get(task.task_id, key, prompt_version)
            if hit is not None:
                responses[task.task_id] = (hit, True)
            else:
                misses.append((task, image))
        workers = max(1, min(concurrency or spec.concurrency, len(misses) or 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_ask, adapter, task, image): task for task, image in misses}
            try:
                for future in as_completed(futures):
                    task = futures[future]
                    response = future.result()
                    if response.error is None:
                        cache.put(task.task_id, key, prompt_version, response)
                    responses[task.task_id] = (response, False)
            except FatalAdapterError as exc:
                for pending in futures:
                    pending.cancel()
                raise SystemExit(f"vtf eval: {exc}") from exc

    records = [
        _record(task, key, *responses[task.task_id], prompt_version, not blind) for task, _ in tasks
    ]
    output_modes = Counter(
        str(responses[t.task_id][0].meta.get("output_mode", "unknown"))
        for t, _ in tasks
        if responses[t.task_id][0].error is None
    )
    summary = summarise(
        records,
        model=key,
        model_id=adapter.model_id or spec.model_id,
        provider=adapter.provider or spec.provider,
        api_version=adapter.api_version,
        prompt_version=prompt_version,
        settings=adapter.settings,
        price=spec.price_usd_per_mtok,
        output_modes=output_modes,
        with_image=not blind,
    )
    out_dir = RunLayout(run_dir).eval_dir(key)
    write_jsonl(out_dir / EVAL_RESULTS, records)
    write_model(out_dir / EVAL_SUMMARY, summary)
    selection = out_dir / SELECTION_FILE
    if sample is not None or task_ids is not None:
        payload = {
            "sample": sample,
            "stratify": list(stratify) if sample is not None else None,
            "seed": seed,
            "limit": limit,
            "explicit_task_ids": task_ids is not None,
            "n": len(tasks),
            "task_ids": [t.task_id for t, _ in tasks],
        }
        selection.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    else:
        selection.unlink(missing_ok=True)

    echo(format_summary(summary, str(run_dir)))
    failed = sum(response.error is not None for response, _ in responses.values())
    if failed:
        echo(f"  {failed} call(s) failed with no reply; failures are not cached, rerun to retry")
    echo(f"  wrote {out_dir / EVAL_RESULTS} and {EVAL_SUMMARY}")
    return summary
