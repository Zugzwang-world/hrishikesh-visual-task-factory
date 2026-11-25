"""Ask a set of tasks to Claude settings through Message Batches, under an eval condition
(brief VTF-08): the trap check on the digital twins, the hero layouts, and the "scale stated"
re-ask of frozen v1's distance and free-space tasks.

Same harness as scripts/model_sweep.py: the standard request body (AnthropicAdapter.request), the
reply cache, the scorer and summary, batches at half price. A condition only changes the question
text and the cache's prompt version:

  none   the task's own question, cache key PROMPT_VERSION (shared with every earlier run, so a
         task already answered is never asked again)
  scale  vtf.eval.scale.stated_question, cache key PROMPT_VERSION+scale.1

Everything is written under <run>/<name>/ (state.json, task_set.json, costs.json,
<model>/results.jsonl, summary.json); nothing else in the run is touched.

  run      submit what the cache lacks, wait, collect, one re-send round, then write results;
           resumable from state.json (an open batch is collected, never re-sent)
  results  results.jsonl and summary.json per model from the cache (no calls)

  nohup uv run python scripts/ask_sweep.py run --run out/v1 --name sweep_scale \
      --condition scale --tasks <ids.json> > out/logs/scale_sweep.log 2>&1 &

Founder rule: never a Fable or Mythos model (the Anthropic adapter refuses them).
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

from vtf.core.io import read_scene, read_task, write_jsonl, write_model
from vtf.core.layout import RunLayout
from vtf.core.task import TaskPackage
from vtf.eval import batch as batches
from vtf.eval.adapters import make_adapter
from vtf.eval.adapters.base import AdapterResponse
from vtf.eval.cache import EvalCache
from vtf.eval.config import resolve_model
from vtf.eval.prompt import PROMPT_VERSION
from vtf.eval.runner import _record, manifest_rows
from vtf.eval.scale import scale_prompt_version, stated_question
from vtf.eval.summary import summarise

MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5-5-xhigh"]
BATCH_DISCOUNT = 0.5


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(f"[{now()}] {msg}", flush=True)


class Condition:
    def __init__(self, name: str, run: Path) -> None:
        self.name = name
        self.run = run
        self.prompt_version = PROMPT_VERSION if name == "none" else scale_prompt_version()
        self._scenes: dict[str, Any] = {}

    def scene(self, scene_id: str):
        if scene_id not in self._scenes:
            lay = RunLayout(self.run)
            path = lay.scene_file(scene_id)
            if not path.is_file():  # a G6 twin scene lives under <run>/twins/
                path = lay.twin_layout().scene_file(scene_id)
            self._scenes[scene_id] = read_scene(path)
        return self._scenes[scene_id]

    def question(self, task: TaskPackage) -> str:
        return task.question if self.name == "none" else stated_question(task, self.scene)


def load_tasks(run: Path, ids: list[str]) -> dict[str, tuple[TaskPackage, Path]]:
    by_id = {r.task_id: r for r in manifest_rows(run)}
    out = {}
    for tid in ids:
        task_file = RunLayout(run).root / by_id[tid].path
        task = read_task(task_file)
        out[tid] = (task, task_file.parent / task.image)
    return out


class State:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = (
            json.loads(path.read_text()) if path.is_file() else {"models": {}, "events": []}
        )

    def model(self, key: str) -> dict[str, Any]:
        return self.data["models"].setdefault(key, {"status": "pending", "batches": []})

    def event(self, msg: str) -> None:
        self.data["events"].append({"at": now(), "msg": msg})
        log(msg)
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2) + "\n")
        tmp.replace(self.path)


def open_ids(m: dict[str, Any]) -> set[str]:
    return {t for b in m["batches"] if not b.get("collected") for t in b["task_ids"]}


def submit(state: State, key: str, adapter: Any, tasks: dict, cond: Condition, ids: list[str],
           kind: str) -> None:  # fmt: skip
    m = state.model(key)
    reqs = []
    for tid in ids:
        task, image = tasks[tid]
        params = adapter.request(image, cond.question(task), task.answer_type,
                                 adapter.structured_output)  # fmt: skip
        size = len(json.dumps(params))
        reqs.append(batches.BatchRequest(custom_id=tid, params=params, size=size))
    for group in batches.chunks(reqs):
        batch_id = batches.submit(adapter.client, group)
        m["batches"].append({"id": batch_id, "kind": kind, "task_ids": [r.custom_id for r in group],
                             "submitted_at": now(), "collected": False})  # fmt: skip
        state.save()
        log(f"{key}: submitted {kind} batch {batch_id} ({len(group)} requests)")


def collect(state: State, key: str, adapter: Any, cache: EvalCache, pv: str) -> None:
    m = state.model(key)
    for b in m["batches"]:
        if b.get("collected"):
            continue
        info = batches.status(adapter.client, b["id"])
        if info["processing_status"] != batches.ENDED:
            continue
        errs = []
        for tid, resp in batches.collect(adapter.client, b["id"], adapter).items():
            if resp.error is None:
                cache.put(tid, key, pv, resp)
            else:
                errs.append({"task_id": tid, "error": resp.error})
        b.update(collected=True, collected_at=now(), errors=errs)
        state.save()
        log(f"{key}: collected batch {b['id']} ({len(b['task_ids']) - len(errs)} ok, "
            f"{len(errs)} errors)")  # fmt: skip


def cmd_run(args: argparse.Namespace, run: Path, ids: list[str], cond: Condition) -> None:
    out = run / args.name
    state = State(out / "state.json")
    state.data.update(pid=os.getpid(), condition=cond.name, prompt_version=cond.prompt_version)
    state.event(f"driver start: {args.models}, {len(ids)} tasks, condition {cond.name}")
    tasks = load_tasks(run, ids)
    adapters = {k: make_adapter(resolve_model(k)) for k in args.models}
    pv = cond.prompt_version
    with EvalCache(args.cache) as cache:
        for round_no in (1, 2):
            for key in args.models:
                m = state.model(key)
                m["status"] = "running"
                busy = open_ids(m)
                todo = [t for t in ids if cache.get(t, key, pv) is None and t not in busy]
                if todo:
                    submit(state, key, adapters[key], tasks, cond, todo,
                           "ask" if round_no == 1 else "resend")  # fmt: skip
                else:
                    log(f"{key}: nothing to send in round {round_no}")
            while any(open_ids(state.model(k)) for k in args.models):
                for key in args.models:
                    collect(state, key, adapters[key], cache, pv)
                if any(open_ids(state.model(k)) for k in args.models):
                    time.sleep(args.poll)
        for key in args.models:
            m = state.model(key)
            m["status"] = "done"
            m["unanswered"] = [t for t in ids if cache.get(t, key, pv) is None]
            state.event(f"{key}: done, {len(m['unanswered'])} of {len(ids)} without a reply")
    for key in args.models:
        write_results(run, args, ids, key, cond)
    state.event("driver end")


def write_results(run: Path, args: argparse.Namespace, ids: list[str], key: str,
                  cond: Condition) -> Any:  # fmt: skip
    tasks = load_tasks(run, ids)
    spec = resolve_model(key)
    adapter = make_adapter(spec)
    defaults = adapter.call_meta()
    pv = cond.prompt_version
    records, modes, questions = [], Counter(), {}
    cost, tok = 0.0, Counter()
    price = spec.price_usd_per_mtok
    with EvalCache(args.cache) as cache:
        for tid in ids:
            task, _ = tasks[tid]
            hit = cache.get(tid, key, pv)
            resp = hit if hit is not None else AdapterResponse(raw_text="", error="no reply")
            if hit is not None:
                modes[str(hit.meta.get("output_mode", "unknown"))] += 1
                disc = BATCH_DISCOUNT if hit.meta.get("via") == "batch" else 1.0
                cost += ((hit.input_tokens or 0) * price.input
                         + (hit.output_tokens or 0) * price.output) / 1e6 * disc  # fmt: skip
                tok["input"] += hit.input_tokens or 0
                tok["output"] += hit.output_tokens or 0
            records.append(_record(task, key, resp, False, pv, True, defaults))
            questions[tid] = cond.question(task)
    summary = summarise(
        records,
        model=key,
        model_id=spec.model_id,
        provider=spec.provider,
        api_version=adapter.api_version,
        prompt_version=pv,
        settings={**adapter.settings, "condition": cond.name},
        price=price,
        output_modes=modes,
    )
    out = run / args.name / key
    write_jsonl(out / "results.jsonl", records)
    write_model(out / "summary.json", summary)
    (out / "questions.json").write_text(json.dumps(questions, indent=1) + "\n")
    costs_path = run / args.name / "costs.json"
    costs = json.loads(costs_path.read_text()) if costs_path.is_file() else {}
    costs[key] = {"replies": sum(r.error != "no reply" for r in records), "of": len(ids),
                  "tokens": dict(tok), "cost_usd": round(cost, 4)}  # fmt: skip
    costs_path.write_text(json.dumps(costs, indent=2) + "\n")
    log(f"{key}: {out}/results.jsonl, accuracy {summary.accuracy:.1%}, cost ${cost:.2f}")
    return summary


def main(argv: list[str] | None = None) -> None:
    load_dotenv(find_dotenv(usecwd=True))
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command", choices=["run", "results"])
    p.add_argument("--run", required=True)
    p.add_argument("--name", required=True, help="results folder under the run, e.g. sweep_scale")
    p.add_argument("--tasks", type=Path, default=None, help="JSON with task_ids; default: all")
    p.add_argument("--condition", choices=["none", "scale"], default="none")
    p.add_argument("--models", nargs="+", default=MODELS)
    p.add_argument("--cache", type=Path, default=None)
    p.add_argument("--poll", type=float, default=30.0)
    args = p.parse_args(argv)
    if any(w in m.lower() for m in args.models for w in ("fable", "mythos")):
        raise SystemExit("never a Fable or Mythos model")
    run = Path(args.run).resolve()
    args.cache = Path(args.cache or os.environ.get("VTF_EVAL_CACHE")
                      or run.parent / "cache" / "eval_cache.sqlite")  # fmt: skip
    os.environ["VTF_EVAL_CACHE"] = str(args.cache)
    out = run / args.name
    out.mkdir(parents=True, exist_ok=True)
    ts_path = out / "task_set.json"
    if args.tasks is not None:
        data = json.loads(args.tasks.read_text())
        ids = data["task_ids"] if isinstance(data, dict) else list(data)
        ts_path.write_text(json.dumps({"made_at": now(), "n": len(ids), "task_ids": ids}) + "\n")
    elif ts_path.is_file():
        ids = json.loads(ts_path.read_text())["task_ids"]
    else:
        ids = [r.task_id for r in manifest_rows(run)]
        ts_path.write_text(json.dumps({"made_at": now(), "n": len(ids), "task_ids": ids}) + "\n")
    cond = Condition(args.condition, run)
    if args.command == "run":
        cmd_run(args, run, ids, cond)
    else:
        for key in args.models:
            write_results(run, args, ids, key, cond)


if __name__ == "__main__":
    sys.exit(main())
