"""Claude model sweep on frozen v1 (brief 06): every model answers the same tasks.

The task set is the 1,500-task eval sample Sonnet answered plus all 150 audit tasks (1,567 in all
for v1). Requests go through the Message Batches API with the standard request body, so batched
and standard replies share the reply cache. Everything is written under <run>/sweep/; the frozen
files (<run>/eval/claude-sonnet-5-5/, manifest, audit) are only read.

  tasks    write sweep/task_set.json (the task set and the fallback subset)
  smoke    one standard call per model on one task image
  proof    one small batch, checked against the standard request body
  run      the detached driver: per model a 100-task gate batch (error budget, cost projection),
           then the rest in chunks; resumable from sweep/state.json; never re-sends a cached task
  results  results.jsonl and summary.json per model from the cache (no calls)
  costs    running cost per model and the projection after its first 100 calls

Founder rule: never a Fable or Mythos model (the Anthropic adapter refuses them).
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

from vtf.core.io import read_task, write_jsonl, write_model
from vtf.core.layout import RunLayout
from vtf.core.rng import derive_seed, make_rng
from vtf.core.task import TaskPackage
from vtf.eval import batch as batches
from vtf.eval.adapters import make_adapter
from vtf.eval.adapters.base import AdapterResponse
from vtf.eval.cache import EvalCache
from vtf.eval.config import resolve_model
from vtf.eval.prompt import PROMPT_VERSION
from vtf.eval.runner import FINAL_STOPS, _ask, _record, manifest_rows, stratified_sample
from vtf.eval.summary import summarise

SWEEP = "sweep"
EVAL_SAMPLE_FROM = "eval/claude-sonnet-5-5/selection.json"
AUDIT_SAMPLE = "audit/sample.json"
GATE = 100
ERROR_BUDGET = 0.05
BATCH_DISCOUNT = 0.5
SUBSET_SEED = 6


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(f"[{now()}] {msg}", flush=True)


# --- task set ---------------------------------------------------------------------------------


def sweep_dir(run: Path) -> Path:
    return run / SWEEP


def build_task_set(run: Path) -> dict[str, Any]:
    """The eval sample plus the audit tasks (manifest order) and the fallback subset."""
    rows = manifest_rows(run)
    by_id = {r.task_id: r for r in rows}
    eval_ids = set(json.loads((run / EVAL_SAMPLE_FROM).read_text())["task_ids"])
    audit_ids = set(json.loads((run / AUDIT_SAMPLE).read_text())["task_ids"])
    union = [r.task_id for r in rows if r.task_id in eval_ids | audit_ids]
    eval_rows = [by_id[t] for t in union if t in eval_ids]

    # Fallback subset (used only if a fallback calls for it): all conflict tasks of the eval
    # sample, as many non-conflict spatial tasks matched on family and camera, 174 non-spatial
    # tasks (stratified by family x rig), plus every audit task.
    conflict = [r for r in eval_rows if r.shortcut_conflict is True]
    nonconf = [r for r in eval_rows if r.shortcut_conflict is False]
    nonspatial = [r for r in eval_rows if r.shortcut_conflict is None]
    need = Counter((r.family, r.camera_id) for r in conflict)
    pool: dict[tuple[str, str | None], list] = defaultdict(list)
    for r in nonconf:
        pool[(r.family, r.camera_id)].append(r)
    matched: list[str] = []
    short: dict[str, int] = {}
    for stratum, k in sorted(need.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        members = sorted(pool[stratum], key=lambda r: r.task_id)
        rng = make_rng(derive_seed(SUBSET_SEED, "matched", *map(str, stratum)))
        take = [members[int(i)].task_id for i in rng.permutation(len(members))[:k]]
        matched += take
        if len(take) < k:
            short["|".join(map(str, stratum))] = k - len(take)
    # Strata with too few non-conflict tasks: top up from the same family, other cameras.
    for key, missing in sorted(short.items()):
        family = key.split("|")[0]
        extra = sorted(
            (r for r in nonconf if r.family == family and r.task_id not in matched),
            key=lambda r: r.task_id,
        )
        rng = make_rng(derive_seed(SUBSET_SEED, "topup", key))
        matched += [extra[int(i)].task_id for i in rng.permutation(len(extra))[:missing]]
    n_nonspatial = max(0, 600 - len(conflict) - len(matched))
    picked = stratified_sample(nonspatial, n_nonspatial, ("family", "rig"), SUBSET_SEED)
    nonsp = [r.task_id for r in picked]
    subset = set(r.task_id for r in conflict) | set(matched) | set(nonsp) | audit_ids
    return {
        "made_at": now(),
        "run": str(run),
        "n": len(union),
        "eval_sample": len(eval_ids),
        "audit": len(audit_ids),
        "audit_not_in_eval": len(audit_ids - eval_ids),
        "task_ids": union,
        "audit_ids": sorted(audit_ids),
        "fallback_subset": {
            "conflict": len(conflict),
            "matched_nonconflict": len(matched),
            "matched_short_strata": short,
            "nonspatial": len(nonsp),
            "plus_audit": len(audit_ids),
            "n": len(subset),
            "task_ids": [t for t in union if t in subset],
        },
    }


def load_task_set(run: Path) -> dict[str, Any]:
    path = sweep_dir(run) / "task_set.json"
    if not path.is_file():
        data = build_task_set(run)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")
    return json.loads(path.read_text())


def load_tasks(run: Path, ids: list[str]) -> dict[str, tuple[TaskPackage, Path]]:
    by_id = {r.task_id: r for r in manifest_rows(run)}
    out = {}
    for tid in ids:
        task_file = RunLayout(run).root / by_id[tid].path
        task = read_task(task_file)
        out[tid] = (task, task_file.parent / task.image)
    return out


def submission_order(key: str, ids: list[str], subset: list[str], subset_first: bool) -> list[str]:
    """A seeded shuffle, so the first 100 (the gate) is a fair sample; optionally subset first."""
    rng = make_rng(derive_seed(SUBSET_SEED, "order", key))
    order = [ids[int(i)] for i in rng.permutation(len(ids))]
    if subset_first:
        s = set(subset)
        order = [t for t in order if t in s] + [t for t in order if t not in s]
    return order


# --- state ------------------------------------------------------------------------------------


class State:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = (
            json.loads(path.read_text()) if path.is_file() else {"models": {}, "events": []}
        )

    def model(self, key: str) -> dict[str, Any]:
        return self.data["models"].setdefault(
            key,
            {
                "status": "pending",
                "gate_checked": False,
                "batches": [],
                "batch_failures": 0,
                "mode": "batch",
            },
        )

    def event(self, msg: str) -> None:
        self.data["events"].append({"at": now(), "msg": msg})
        log(msg)
        self.save()

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2) + "\n")
        tmp.replace(self.path)


def cached_ids(cache: EvalCache, key: str, ids: list[str]) -> set[str]:
    return {t for t in ids if cache.get(t, key, PROMPT_VERSION) is not None}


def open_ids(ms: dict[str, Any]) -> set[str]:
    return {t for b in ms["batches"] if not b.get("collected") for t in b["task_ids"]}


# --- batch steps ------------------------------------------------------------------------------


def submit_batches(
    state: State, key: str, adapter: Any, tasks: dict, ids: list[str], kind: str
) -> list[str]:
    """Submit `ids` in chunks; each batch id is saved before the next chunk is built."""
    ms = state.model(key)
    made = []
    reqs = (batches.build_request(adapter, *tasks[t]) for t in ids)
    for group in batches.chunks(reqs):
        tries = 0
        while True:
            try:
                batch_id = batches.submit(adapter.client, group)
                break
            except Exception as exc:  # noqa: BLE001 - any API failure counts as a batch failure
                tries += 1
                ms["batch_failures"] += 1
                state.event(f"{key}: batch create failed ({tries}): {exc!r}")
                if tries >= 2:
                    raise
                time.sleep(30)
        ms["batches"].append(
            {
                "id": batch_id,
                "kind": kind,
                "task_ids": [r.custom_id for r in group],
                "submitted_at": now(),
                "collected": False,
                "mb": round(sum(r.size for r in group) / 1e6, 1),
            }
        )
        state.save()
        made.append(batch_id)
        log(f"{key}: submitted {kind} batch {batch_id} ({len(group)} requests)")
    return made


def collect_ended(state: State, key: str, adapter: Any, cache: EvalCache) -> dict[str, Any]:
    """Collect every ended, uncollected batch of `key` into the cache. Returns stats."""
    ms = state.model(key)
    stats = Counter()
    for b in ms["batches"]:
        if b.get("collected"):
            continue
        info = batches.status(adapter.client, b["id"])
        b["last_status"] = info
        if info["processing_status"] != batches.ENDED:
            continue
        responses = batches.collect(adapter.client, b["id"], adapter)
        errs = 0
        for tid, resp in responses.items():
            if resp.error is None:
                cache.put(tid, key, PROMPT_VERSION, resp)
                stats["ok"] += 1
                if resp.stop_reason not in FINAL_STOPS:
                    stats["nonfinal"] += 1
            else:
                errs += 1
                stats["error"] += 1
                b.setdefault("errors", []).append({"task_id": tid, "error": resp.error})
        b["collected"] = True
        b["collected_at"] = now()
        b["results"] = {"ok": len(responses) - errs, "error": errs}
        if responses and errs / len(responses) > 0.5:
            ms["batch_failures"] += 1
            state.event(f"{key}: batch {b['id']} mostly errored ({errs}/{len(responses)})")
        state.save()
        log(f"{key}: collected batch {b['id']}: {b['results']}")
    return dict(stats)


def wait_open(
    state: State,
    key: str,
    adapter: Any,
    cache: EvalCache,
    poll_s: float = 30,
    others: Any = None,
) -> None:
    """Wait for `key`'s open batches; `others()` collects other models' ended batches meanwhile."""
    while open_ids(state.model(key)):
        collect_ended(state, key, adapter, cache)
        if others is not None:
            others()
        if open_ids(state.model(key)):
            time.sleep(poll_s)


def collect_others(state: State, cache: EvalCache, run: Path, cache_path: Path, keys: list[str]):
    adapters: dict[str, Any] = {}

    def collect() -> None:
        for key in keys:
            ms = state.model(key)
            if ms["status"] == "running" and open_ids(ms):
                if key not in adapters:
                    adapters[key] = make_adapter(resolve_model(key))
                collect_ended(state, key, adapters[key], cache)
        cost_report(run, cache_path, keys)

    return collect


# --- standard calls (fallback) ----------------------------------------------------------------


def run_standard(
    key: str, adapter: Any, tasks: dict, ids: list[str], cache: EvalCache, concurrency: int
) -> Counter:
    stats: Counter = Counter()
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(_ask, adapter, *tasks[t]): t for t in ids}
        for i, fut in enumerate(as_completed(futures), 1):
            tid = futures[fut]
            resp = fut.result()
            if resp.error is None:
                cache.put(tid, key, PROMPT_VERSION, resp)
                stats["ok"] += 1
                if resp.stop_reason not in FINAL_STOPS:
                    stats["nonfinal"] += 1
            else:
                stats["error"] += 1
                log(f"{key}: standard call failed for {tid}: {resp.error}")
            if i % 25 == 0:
                log(f"{key}: standard calls {i}/{len(ids)} {dict(stats)}")
    return stats


# --- costs ------------------------------------------------------------------------------------


def cost_report(run: Path, cache_path: Path, keys: list[str]) -> dict[str, Any]:
    import sqlite3

    task_set = load_task_set(run)
    ids = set(task_set["task_ids"])
    con = sqlite3.connect(cache_path)
    out: dict[str, Any] = {"at": now(), "n_tasks": len(ids), "models": {}}
    for key in keys:
        spec = resolve_model(key)
        price = spec.price_usd_per_mtok
        rows = con.execute(
            "SELECT task_id, input_tokens, output_tokens, meta, created_at FROM replies "
            "WHERE model = ? AND prompt_version = ? ORDER BY created_at, rowid",
            (key, PROMPT_VERSION),
        ).fetchall()
        rows = [r for r in rows if r[0] in ids]
        per_call = []
        tok = Counter()
        via = Counter()
        for _tid, tin, tout, meta, _at in rows:
            m = json.loads(meta)
            v = m.get("via", "standard")
            via[v] += 1
            disc = BATCH_DISCOUNT if v == "batch" else 1.0
            cost = ((tin or 0) * price.input + (tout or 0) * price.output) / 1e6 * disc
            per_call.append(cost)
            tok["input"] += tin or 0
            tok["output"] += tout or 0
            tok["thinking"] += m.get("thinking_tokens") or 0
        tonight = [c for c, r in zip(per_call, rows, strict=True) if json.loads(r[3]).get("via")]
        first100 = tonight[:GATE]
        proj = None
        if len(first100) >= GATE:
            proj = round(sum(first100) / len(first100) * len(ids), 2)
        out["models"][key] = {
            "replies": len(rows),
            "of": len(ids),
            "via": dict(via),
            "tokens": dict(tok),
            "cost_usd": round(sum(per_call), 4),
            "cost_usd_tonight": round(sum(tonight), 4),
            "projected_total_usd_after_first_100": proj,
            "price_usd_per_mtok": price.model_dump(),
            "batch_discount": BATCH_DISCOUNT,
        }
    path = sweep_dir(run) / "costs.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    return out


# --- results ----------------------------------------------------------------------------------


def write_results(run: Path, cache_path: Path, key: str, ids: list[str] | None = None) -> Any:
    task_set = load_task_set(run)
    ids = ids or task_set["task_ids"]
    tasks = load_tasks(run, ids)
    spec = resolve_model(key)
    adapter = make_adapter(spec)
    defaults = adapter.call_meta()
    records = []
    modes: Counter = Counter()
    sweep_started = task_set["made_at"]
    with EvalCache(cache_path) as cache:
        for tid in ids:
            task, _ = tasks[tid]
            hit = cache.get(tid, key, PROMPT_VERSION)
            if hit is None:
                resp, cached = AdapterResponse(raw_text="", error="no reply"), False
            else:
                resp = hit
                cached = "via" not in hit.meta  # replies from before tonight's sweep
                modes[str(hit.meta.get("output_mode", "unknown"))] += 1
            records.append(_record(task, key, resp, cached, PROMPT_VERSION, True, defaults))
    settings = {
        **adapter.settings,
        "sweep_started": sweep_started,
        "via": dict(Counter(r.via or "standard" for r in records if r.error != "no reply")),
    }
    summary = summarise(
        records,
        model=key,
        model_id=spec.model_id,
        provider=spec.provider,
        api_version=adapter.api_version,
        prompt_version=PROMPT_VERSION,
        settings=settings,
        price=spec.price_usd_per_mtok,
        output_modes=modes,
    )
    out = sweep_dir(run) / key
    write_jsonl(out / "results.jsonl", records)
    write_model(out / "summary.json", summary)
    (out / "task_ids.json").write_text(json.dumps({"n": len(ids), "task_ids": ids}) + "\n")
    log(
        f"{key}: wrote {out}/results.jsonl ({len(records)} records, accuracy "
        f"{summary.accuracy:.1%}, errors {summary.errors}, missing "
        f"{sum(r.error == 'no reply' for r in records)})"
    )
    return summary


# --- commands ---------------------------------------------------------------------------------


def cmd_smoke(args: argparse.Namespace) -> None:
    run = Path(args.run)
    task_set = load_task_set(run)
    for key in args.models:
        spec = resolve_model(key)
        adapter = make_adapter(spec)
        with EvalCache(args.cache) as cache:
            todo = [t for t in task_set["task_ids"] if cache.get(t, key, PROMPT_VERSION) is None]
            if not todo:
                log(f"{key}: nothing uncached to smoke-test")
                continue
            # an object task, so the reply exercises the naming rule and the image
            tasks = load_tasks(run, todo[:40])
            tid = next((t for t in todo[:40] if tasks[t][0].answer_type == "object"), todo[0])
            task, image = tasks[tid]
            ok = False
            for attempt in (1, 2):
                resp = adapter.answer(image, task.question, task.answer_type)
                from vtf.eval.runner import score_task

                score = score_task(task, resp.raw_text) if resp.error is None else None
                log(
                    f"{key} smoke {attempt}: task {tid} family {task.family} "
                    f"error={resp.error} stop={resp.stop_reason} raw={resp.raw_text!r} "
                    f"parse_ok={score.parse_ok if score else None} "
                    f"correct={score.correct if score else None} truth={task.answer!r} "
                    f"tokens in/out/thinking={resp.input_tokens}/{resp.output_tokens}/"
                    f"{resp.meta.get('thinking_tokens')} served={resp.meta.get('served_model')} "
                    f"mode={resp.meta.get('output_mode')} latency={resp.latency_s}s"
                )
                if resp.error is None:
                    cache.put(tid, key, PROMPT_VERSION, resp)
                if (
                    resp.error is None
                    and score
                    and score.parse_ok
                    and resp.meta.get("output_mode") == "json_schema"
                ):
                    ok = True
                    break
            print(json.dumps({"model": key, "smoke_ok": ok}), flush=True)


def cmd_proof(args: argparse.Namespace) -> None:
    run = Path(args.run)
    task_set = load_task_set(run)
    key = args.models[0]
    adapter = make_adapter(resolve_model(key))
    state = State(sweep_dir(run) / "state.json")
    with EvalCache(args.cache) as cache:
        done = cached_ids(cache, key, task_set["task_ids"])
        todo = [t for t in task_set["task_ids"] if t not in done][: args.n]
        tasks = load_tasks(run, todo)
        # the batch params are the standard call's kwargs, byte for byte
        for t in todo:
            req = batches.build_request(adapter, *tasks[t])
            std = adapter.request(
                tasks[t][1],
                tasks[t][0].question,
                tasks[t][0].answer_type,
                adapter.structured_output,
            )
            assert json.dumps(req.params, sort_keys=True) == json.dumps(std, sort_keys=True)
        log(f"{key}: proof: {len(todo)} batch params identical to the standard request body")
        submit_batches(state, key, adapter, tasks, todo, "proof")
        wait_open(state, key, adapter, cache, poll_s=15)
        for t in todo:
            hit = cache.get(t, key, PROMPT_VERSION)
            got = (
                None
                if hit is None
                else (
                    hit.raw_text,
                    hit.stop_reason,
                    hit.meta.get("via"),
                    hit.meta.get("thinking_tokens"),
                )
            )
            log(f"{key}: proof {t}: {got}")


def cmd_run(args: argparse.Namespace) -> None:
    run = Path(args.run)
    task_set = load_task_set(run)
    ids = task_set["task_ids"]
    subset = task_set["fallback_subset"]["task_ids"]
    state = State(sweep_dir(run) / "state.json")
    state.data["pid"] = os.getpid()
    state.event(f"driver start: models {args.models}, {len(ids)} tasks")
    all_tasks = load_tasks(run, ids)
    with EvalCache(args.cache) as cache:
        for key in args.models:
            ms = state.model(key)
            if ms["status"] in ("done", "skipped", "paused"):
                log(f"{key}: status {ms['status']}, not touched")
                continue
            spec = resolve_model(key)
            adapter = make_adapter(spec)
            only_subset = ms.get("subset_only", False)
            target = subset if only_subset else ids
            order = submission_order(key, target, subset, key in args.subset_first)
            collect_ended(state, key, adapter, cache)
            ms["status"] = "running"
            state.save()

            def pending(key: str = key, order: list[str] = order, ms: dict = ms) -> list[str]:
                done = cached_ids(cache, key, order)
                busy = open_ids(ms)
                return [t for t in order if t not in done and t not in busy]

            if ms["mode"] == "standard":
                todo = pending()
                state.event(f"{key}: standard calls for {len(todo)} tasks")
                run_standard(key, adapter, all_tasks, todo, cache, spec.concurrency)
                continue

            if not ms["gate_checked"]:
                if not any(b["kind"] == "gate" for b in ms["batches"]):
                    gate_ids = pending()[:GATE]
                    try:
                        submit_batches(state, key, adapter, all_tasks, gate_ids, "gate")
                    except Exception:  # noqa: BLE001
                        ms["mode"] = "standard"
                        state.event(f"{key}: batches failed twice; switching to standard calls")
                        run_standard(key, adapter, all_tasks, pending(), cache, spec.concurrency)
                        continue
                others = collect_others(state, cache, run, args.cache, args.models)
                wait_open(state, key, adapter, cache, args.poll, others)
                gate = [b for b in ms["batches"] if b["kind"] == "gate"]
                gids = [t for b in gate for t in b["task_ids"]]
                bad = 0
                for t in gids:
                    hit = cache.get(t, key, PROMPT_VERSION)
                    if hit is None or hit.stop_reason not in FINAL_STOPS:
                        bad += 1
                rate = bad / max(1, len(gids))
                ms["gate"] = {"n": len(gids), "bad": bad, "rate": round(rate, 4)}
                costs = cost_report(run, args.cache, [key])["models"][key]
                ms["gate"]["projected_total_usd"] = costs["projected_total_usd_after_first_100"]
                ms["gate"]["cost_so_far_usd"] = costs["cost_usd_tonight"]
                state.event(f"{key}: gate {ms['gate']}")
                if rate > ERROR_BUDGET and len(gids) >= 20:
                    ms["status"] = "paused"
                    state.event(f"{key}: PAUSED, error or truncation rate {rate:.1%} > 5%")
                    continue
                ms["gate_checked"] = True
                state.save()

            rest = pending()
            if rest:
                try:
                    submit_batches(state, key, adapter, all_tasks, rest, "rest")
                except Exception:  # noqa: BLE001
                    ms["mode"] = "standard"
                    state.event(f"{key}: batches failed twice; switching to standard calls")
                    run_standard(key, adapter, all_tasks, pending(), cache, spec.concurrency)

        # wait for everything still open, collecting as batches end; one re-send round for
        # requests that errored or expired, then standard calls for anything left
        for round_no in (1, 2):
            while True:
                open_any = False
                for key in args.models:
                    ms = state.model(key)
                    if ms["status"] != "running":
                        continue
                    adapter = make_adapter(resolve_model(key))
                    collect_ended(state, key, adapter, cache)
                    open_any |= bool(open_ids(ms))
                    cost_report(run, args.cache, args.models)
                if not open_any:
                    break
                time.sleep(args.poll)
            for key in args.models:
                ms = state.model(key)
                if ms["status"] != "running":
                    continue
                spec = resolve_model(key)
                adapter = make_adapter(spec)
                target = subset if ms.get("subset_only") else ids
                missing = [t for t in target if cache.get(t, key, PROMPT_VERSION) is None]
                if not missing:
                    ms["status"] = "done"
                    state.event(f"{key}: all {len(target)} tasks answered")
                    continue
                if round_no == 1 and ms["mode"] == "batch":
                    state.event(f"{key}: re-sending {len(missing)} errored or expired requests")
                    try:
                        submit_batches(state, key, adapter, all_tasks, missing, "resend")
                    except Exception:  # noqa: BLE001
                        ms["mode"] = "standard"
                else:
                    state.event(f"{key}: standard calls for the last {len(missing)} tasks")
                    run_standard(key, adapter, all_tasks, missing, cache, spec.concurrency)
                    still = [t for t in target if cache.get(t, key, PROMPT_VERSION) is None]
                    ms["status"] = "done"
                    ms["unanswered"] = still
                    state.event(f"{key}: done, {len(still)} tasks without a reply")
            state.save()

    for key in args.models:
        if state.model(key)["status"] == "done":
            sub = subset if state.model(key).get("subset_only") else None
            write_results(run, args.cache, key, sub)
    cost_report(run, args.cache, args.models)
    state.event("driver end")


def cmd_results(args: argparse.Namespace) -> None:
    run = Path(args.run)
    task_set = load_task_set(run)
    for key in args.models:
        ids = task_set["fallback_subset"]["task_ids"] if key in args.subset_only else None
        write_results(run, args.cache, key, ids)


def cmd_costs(args: argparse.Namespace) -> None:
    print(json.dumps(cost_report(Path(args.run), args.cache, args.models), indent=2))


def main(argv: list[str] | None = None) -> None:
    load_dotenv(find_dotenv(usecwd=True))
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command", choices=["tasks", "smoke", "proof", "run", "results", "costs"])
    p.add_argument("--run", required=True)
    p.add_argument("--cache", type=Path, default=None)
    p.add_argument("--models", nargs="+", default=[])
    p.add_argument("--subset-first", nargs="*", default=[])
    p.add_argument("--subset-only", nargs="*", default=[])
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--poll", type=float, default=60.0)
    args = p.parse_args(argv)
    run = Path(args.run).resolve()
    args.run = str(run)
    default_cache = run.parent / "cache" / "eval_cache.sqlite"
    args.cache = Path(args.cache or os.environ.get("VTF_EVAL_CACHE") or default_cache)
    os.environ["VTF_EVAL_CACHE"] = str(args.cache)
    if args.command == "tasks":
        data = load_task_set(run)
        print(
            json.dumps(
                {k: v for k, v in data.items() if k not in ("task_ids", "audit_ids")}
                | {
                    "fallback_subset": {
                        k: v for k, v in data["fallback_subset"].items() if k != "task_ids"
                    }
                },
                indent=2,
            )
        )
        return
    {
        "smoke": cmd_smoke,
        "proof": cmd_proof,
        "run": cmd_run,
        "results": cmd_results,
        "costs": cmd_costs,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
