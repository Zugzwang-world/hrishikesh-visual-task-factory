"""Answers for the G6 twin pairs (brief VTF-07, lane B): every Claude setting on every task of
every counterfactual twin pair, plus the one refreshed-audit task no model had answered.

The 1,567-task sweep (scripts/model_sweep.py) already answered one or both tasks of most pairs.
This driver asks only the rest, through the same harness: the same request body, prompts,
scorer, settings and reply cache, Message Batches at half price. Nothing in the cache is asked
again. Everything is written under <run>/sweep_twins/ (task_set.json, state.json, costs.json,
<model>/results.jsonl); <run>/sweep/ and the frozen v1 files are only read.

  tasks    write sweep_twins/task_set.json (the missing tasks of the 484 pairs + the audit task)
  run      the detached driver: per model a 100-task gate batch (error budget and projected
           cost), a budget check over all models, then the rest; resumable from state.json
  results  results.jsonl and summary.json per model from the cache (no calls)
  costs    cost so far and the projection after each model's first 100 calls

Founder rule: never a Fable or Mythos model (the Anthropic adapter refuses them).

  nohup uv run python scripts/twin_sweep.py run --run out/v1 > out/logs/twin_sweep.log 2>&1 &
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

import model_sweep as ms  # noqa: E402

from vtf.core.io import read_task  # noqa: E402
from vtf.core.layout import RunLayout  # noqa: E402
from vtf.core.rng import derive_seed, make_rng  # noqa: E402
from vtf.eval.adapters import make_adapter  # noqa: E402
from vtf.eval.cache import EvalCache  # noqa: E402
from vtf.eval.config import resolve_model  # noqa: E402
from vtf.eval.prompt import PROMPT_VERSION  # noqa: E402
from vtf.eval.runner import FINAL_STOPS, manifest_rows  # noqa: E402

ms.SWEEP = "sweep_twins"  # every model_sweep helper now reads and writes <run>/sweep_twins/
MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5-5-xhigh"]
AUDIT_V2 = "audit/sample_v2.json"
BUDGET_USD = 30.0
SEED = 7


def log(msg: str) -> None:
    ms.log(msg)


# --- task set ---------------------------------------------------------------------------------


def twin_pairs(run: Path) -> list[dict[str, str]]:
    """Every accepted G6 twin task with the base task it was made from (manifest order)."""
    lay = RunLayout(run)
    pairs = []
    for row in manifest_rows(run):
        if "_twin_" not in row.scene_id:
            continue
        task = read_task(lay.root / row.path)
        if task.twin_of:
            pairs.append(
                {
                    "base": task.twin_of,
                    "twin": row.task_id,
                    "family": row.family,
                    "scene": row.scene_id.split("_twin_")[0],
                }
            )
    return pairs


def build_task_set(run: Path) -> dict[str, Any]:
    answered = set(json.loads((run / "sweep" / "task_set.json").read_text())["task_ids"])
    pairs = twin_pairs(run)
    want = {t for p in pairs for t in (p["base"], p["twin"])}
    audit: list[str] = []
    sample = run / AUDIT_V2
    if sample.is_file():
        audit = [
            r["new"]
            for r in json.loads(sample.read_text()).get("replacements", [])
            if not r.get("models_answered")
        ]
    need = (want | set(audit)) - answered
    ids = [r.task_id for r in manifest_rows(run) if r.task_id in need]
    return {
        "made_at": ms.now(),
        "run": f"out/{run.name}",
        "n": len(ids),
        "pairs": len(pairs),
        "pairs_fully_answered_before": sum(
            p["base"] in answered and p["twin"] in answered for p in pairs
        ),
        "audit_ids": audit,
        "task_ids": ids,
        "fallback_subset": {"n": 0, "task_ids": []},
    }


def ensure_task_set(run: Path) -> dict[str, Any]:
    path = ms.sweep_dir(run) / "task_set.json"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(build_task_set(run), indent=2) + "\n")
    return json.loads(path.read_text())


def subsample(run: Path, ids: list[str], keep_share: float, audit: list[str]) -> list[str]:
    """Whole pairs in a seeded order until `keep_share` of the tasks; the audit task always."""
    pairs = twin_pairs(run)
    rng = make_rng(derive_seed(SEED, "pairs"))
    order = [pairs[int(i)] for i in rng.permutation(len(pairs))]
    allowed = set(ids)
    keep: list[str] = [t for t in audit if t in allowed]
    target = int(len(ids) * keep_share)
    for p in order:
        if len(keep) >= target:
            break
        keep += [t for t in (p["base"], p["twin"]) if t in allowed and t not in keep]
    return [t for t in ids if t in set(keep)]


# --- driver -----------------------------------------------------------------------------------


def wait_all(state: ms.State, keys: list[str], adapters: dict, cache: EvalCache, poll: float):
    while True:
        busy = False
        for key in keys:
            ms.collect_ended(state, key, adapters[key], cache)
            busy |= bool(ms.open_ids(state.model(key)))
        if not busy:
            return
        time.sleep(poll)


def cmd_run(args: argparse.Namespace) -> None:
    run = Path(args.run)
    ts = ensure_task_set(run)
    ids = ts["task_ids"]
    state = ms.State(ms.sweep_dir(run) / "state.json")
    state.data["pid"] = os.getpid()
    state.event(f"driver start: models {args.models}, {len(ids)} tasks, budget ${args.budget}")
    tasks = ms.load_tasks(run, ids)
    adapters = {k: make_adapter(resolve_model(k)) for k in args.models}
    with EvalCache(args.cache) as cache:

        def pending(key: str, target: list[str]) -> list[str]:
            done = ms.cached_ids(cache, key, target)
            busy = ms.open_ids(state.model(key))
            return [t for t in target if t not in done and t not in busy]

        # 1. a 100-task gate batch per model (a seeded shuffle, so a fair sample)
        for key in args.models:
            m = state.model(key)
            if m["status"] in ("done", "paused"):
                continue
            m["status"] = "running"
            if not any(b["kind"] == "gate" for b in m["batches"]):
                order = ms.submission_order(key, ids, [], False)
                gate = pending(key, order)[: ms.GATE]
                if gate:
                    ms.submit_batches(state, key, adapters[key], tasks, gate, "gate")
            state.save()
        wait_all(state, args.models, adapters, cache, args.poll)

        # 2. error budget per model, projected cost over all models against the budget
        if "budget" not in state.data:
            costs = ms.cost_report(run, args.cache, args.models)["models"]
            projected = 0.0
            for key in args.models:
                m = state.model(key)
                gids = [t for b in m["batches"] if b["kind"] == "gate" for t in b["task_ids"]]
                bad = sum(
                    1
                    for t in gids
                    if (hit := cache.get(t, key, PROMPT_VERSION)) is None
                    or hit.stop_reason not in FINAL_STOPS
                )
                rate = bad / max(1, len(gids))
                proj = costs[key]["projected_total_usd_after_first_100"] or costs[key]["cost_usd"]
                m["gate"] = {"n": len(gids), "bad": bad, "rate": round(rate, 4), "projected": proj}
                if rate > ms.ERROR_BUDGET and len(gids) >= 20:
                    m["status"] = "paused"
                    state.event(f"{key}: PAUSED, error or truncation rate {rate:.1%} > 5%")
                else:
                    m["gate_checked"] = True
                    projected += proj
                state.event(f"{key}: gate {m['gate']}")
            share = 1.0 if projected <= args.budget else args.budget / projected * 0.95
            target = ids if share >= 1.0 else subsample(run, ids, share, ts["audit_ids"])
            state.data["budget"] = {
                "budget_usd": args.budget,
                "projected_usd": round(projected, 2),
                "keep_share": round(share, 4),
                "tasks": len(target),
                "task_ids": target,
            }
            state.event(
                f"budget: projected ${projected:.2f} for {len(ids)} tasks x {len(args.models)} "
                f"models (budget ${args.budget}); asking {len(target)} tasks"
            )
        target = state.data["budget"]["task_ids"]

        # 3. the rest, then one re-send round for errored or expired requests
        for round_no in (1, 2):
            for key in args.models:
                m = state.model(key)
                if m["status"] != "running" or not m.get("gate_checked"):
                    continue
                rest = pending(key, target)
                if rest:
                    kind = "rest" if round_no == 1 else "resend"
                    ms.submit_batches(state, key, adapters[key], tasks, rest, kind)
            wait_all(state, args.models, adapters, cache, args.poll)
            ms.cost_report(run, args.cache, args.models)

        for key in args.models:
            m = state.model(key)
            if m["status"] != "running":
                continue
            missing = [t for t in target if cache.get(t, key, PROMPT_VERSION) is None]
            m["status"] = "done"
            m["unanswered"] = missing
            state.event(f"{key}: done, {len(missing)} of {len(target)} tasks without a reply")
    for key in args.models:
        if state.model(key)["status"] == "done":
            ms.write_results(run, args.cache, key, target)
    ms.cost_report(run, args.cache, args.models)
    state.event("driver end")


def main(argv: list[str] | None = None) -> None:
    load_dotenv(find_dotenv(usecwd=True))
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command", choices=["tasks", "run", "results", "costs"])
    p.add_argument("--run", required=True)
    p.add_argument("--cache", type=Path, default=None)
    p.add_argument("--models", nargs="+", default=MODELS)
    p.add_argument("--budget", type=float, default=BUDGET_USD)
    p.add_argument("--poll", type=float, default=60.0)
    args = p.parse_args(argv)
    run = Path(args.run).resolve()
    args.run = str(run)
    default_cache = run.parent / "cache" / "eval_cache.sqlite"
    args.cache = Path(args.cache or os.environ.get("VTF_EVAL_CACHE") or default_cache)
    os.environ["VTF_EVAL_CACHE"] = str(args.cache)
    if args.command == "tasks":
        ts = ensure_task_set(run)
        print(json.dumps({k: v for k, v in ts.items() if k != "task_ids"}, indent=2))
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "results":
        ts = ensure_task_set(run)
        st = ms.State(ms.sweep_dir(run) / "state.json").data
        target = (st.get("budget") or {}).get("task_ids") or ts["task_ids"]
        for key in args.models:
            ms.write_results(run, args.cache, key, target)
    else:
        print(json.dumps(ms.cost_report(run, args.cache, args.models), indent=2))


if __name__ == "__main__":
    sys.exit(main())
