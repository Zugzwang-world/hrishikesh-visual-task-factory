"""vtf eval: run one model on the accepted tasks of a run, score it, write results and a summary.

Reads <run>/manifest.jsonl in order and takes the first `limit` tasks. Cached replies are reused
(no API call); misses go to the adapter on a small thread pool. Writes
<run>/eval/<model>/results.jsonl (one EvalRecord per task, manifest order) and summary.json.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from vtf.core.io import read_jsonl, read_task, write_jsonl, write_model
from vtf.core.layout import EVAL_RESULTS, EVAL_SUMMARY, RunLayout
from vtf.core.task import EvalRecord, ManifestRow, TaskPackage
from vtf.eval.adapters import Adapter, AdapterResponse, FatalAdapterError, make_adapter
from vtf.eval.cache import EvalCache
from vtf.eval.config import resolve_model
from vtf.eval.prompt import PROMPT_VERSION
from vtf.eval.scorer import score_response
from vtf.eval.summary import EvalSummary, format_summary, summarise

FINAL_STOPS = {None, "end_turn", "stop_sequence"}


def load_tasks(run_dir: Path, limit: int | None = None) -> list[tuple[TaskPackage, Path]]:
    """Accepted tasks in manifest order, each with its image path."""
    layout = RunLayout(run_dir)
    if not layout.task_manifest.is_file():
        raise FileNotFoundError(f"{layout.task_manifest} not found: run vtf validate first")
    rows = [ManifestRow.model_validate(r) for r in read_jsonl(layout.task_manifest)]
    if limit is not None:
        rows = rows[:limit]
    tasks = []
    for row in rows:
        task_file = layout.root / row.path
        task = read_task(task_file)
        tasks.append((task, task_file.parent / task.image))
    return tasks


def _record(task: TaskPackage, model: str, response: AdapterResponse, cached: bool) -> EvalRecord:
    score = score_response(task.answer_type, task.answer, response.raw_text, task.tolerance)
    error = response.error
    if error is None and response.stop_reason not in FINAL_STOPS:
        error = f"stop_reason: {response.stop_reason}"
    return EvalRecord(
        task_id=task.task_id,
        model=model,
        prompt_version=PROMPT_VERSION,
        family=task.family,
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


def eval_run(
    run_dir: Path | str,
    model: str = "claude-sonnet-5-5",
    limit: int | None = None,
    models_config: Path | str | None = None,
    *,
    cache_path: Path | str | None = None,
    adapter: Adapter | None = None,
    concurrency: int | None = None,
    echo=print,
) -> EvalSummary:
    """Evaluate `model` on the first `limit` accepted tasks of `run_dir`."""
    load_dotenv(find_dotenv(usecwd=True))
    run_dir = Path(run_dir)
    spec = resolve_model(model, models_config)
    tasks = load_tasks(run_dir, limit)
    try:
        adapter = adapter or make_adapter(spec)
    except FatalAdapterError as exc:
        raise SystemExit(f"vtf eval: {exc}") from exc

    responses: dict[str, tuple[AdapterResponse, bool]] = {}
    with EvalCache(cache_path) as cache:
        misses = []
        for task, image in tasks:
            hit = cache.get(task.task_id, spec.key, PROMPT_VERSION)
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
                        cache.put(task.task_id, spec.key, PROMPT_VERSION, response)
                    responses[task.task_id] = (response, False)
            except FatalAdapterError as exc:
                for pending in futures:
                    pending.cancel()
                raise SystemExit(f"vtf eval: {exc}") from exc

    records = [_record(task, spec.key, *responses[task.task_id]) for task, _ in tasks]
    output_modes = Counter(
        str(responses[t.task_id][0].meta.get("output_mode", "unknown"))
        for t, _ in tasks
        if responses[t.task_id][0].error is None
    )
    summary = summarise(
        records,
        model=spec.key,
        model_id=adapter.model_id or spec.model_id,
        provider=adapter.provider or spec.provider,
        api_version=adapter.api_version,
        prompt_version=PROMPT_VERSION,
        settings=adapter.settings,
        price=spec.price_usd_per_mtok,
        output_modes=output_modes,
    )
    out_dir = RunLayout(run_dir).eval_dir(spec.key)
    write_jsonl(out_dir / EVAL_RESULTS, records)
    write_model(out_dir / EVAL_SUMMARY, summary)

    echo(format_summary(summary, str(run_dir)))
    failed = sum(response.error is not None for response, _ in responses.values())
    if failed:
        echo(f"  {failed} call(s) failed with no reply; failures are not cached, rerun to retry")
    echo(f"  wrote {out_dir / EVAL_RESULTS} and {EVAL_SUMMARY}")
    return summary
