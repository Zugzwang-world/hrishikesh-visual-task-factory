"""`vtf balance <run>`: G5 answer balancing by seeded rejection sampling, after `vtf validate`.

Per family, no answer value may hold more than its cap of the family's tasks (bool 45 to 55%;
any single int, object or set value, the empty set included, at most 35%; see
g5_vision_necessity.CAPS). Over-represented values lose randomly chosen tasks (seeded; tasks
without a G6 twin link go first) until every value is within the cap. Dropped tasks leave
manifest.jsonl and enter rejected.jsonl (stage G5, reason rebalance, detail.task_id); their
folders stay, with a failed G5 gate, so the step can be re-run. Kept tasks get G5 evidence.

The step is a pure function of the validated task set and the seed: it re-pools the tasks of
any earlier rebalance before sampling, so running it twice gives the same manifest.

Writes <run>/baselines/prior.json (prior-only baseline after and before balancing) and
<run>/baselines/balance.json (per-family distributions, drops and the G5 pass rate).
"""

from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from vtf.core.io import read_jsonl, read_task, write_jsonl, write_model
from vtf.core.layout import TASK_FILE, TASKS_DIR, RunLayout
from vtf.core.rng import derive_seed, make_rng
from vtf.core.task import GateResult, ManifestRow, RejectedRow, TaskPackage
from vtf.validate.gates.g5_vision_necessity import (
    BALANCE_FILE,
    BLIND_FILE,
    CAPS,
    PRIOR_FILE,
    capped_counts,
    effective_cap,
    g5_evidence,
    prior_baseline,
    read_json,
    task_key,
    with_gate,
    write_json,
)

BALANCE_VERSION = "0.1.0"
STAGE, REASON = "G5", "rebalance"


def _linked(task: TaskPackage) -> bool:
    return task.twin_of is not None or task.twin_id is not None


def plan_rebalance(
    tasks: list[TaskPackage], seed: int = 0, caps: dict[str, float | None] | None = None
) -> tuple[list[TaskPackage], list[tuple[TaskPackage, dict[str, Any]]], dict[str, Any]]:
    """Split `tasks` into kept and dropped (each dropped task with its reason detail).

    Returns (kept in input order, dropped, per-family report)."""
    caps = {**CAPS, **(caps or {})}
    by_family: dict[str, list[TaskPackage]] = defaultdict(list)
    for task in tasks:
        by_family[task.family].append(task)
    dropped: list[tuple[TaskPackage, dict[str, Any]]] = []
    report: dict[str, Any] = {}
    for family, members in sorted(by_family.items()):
        answer_type = members[0].answer_type
        counts = Counter(task_key(t) for t in members)
        cap = effective_cap(caps.get(answer_type), len(counts))
        entry: dict[str, Any] = {
            "answer_type": answer_type,
            "cap": cap,
            "n_before": len(members),
            "distinct_answers": len(counts),
            "single_answer": len(counts) == 1,
        }
        drops: list[TaskPackage] = []
        if cap is not None:
            limits = capped_counts(dict(counts), cap)
            for key in sorted(counts):
                excess = counts[key] - limits[key]
                if excess <= 0:
                    continue
                candidates = sorted(
                    (t for t in members if task_key(t) == key), key=lambda t: t.task_id
                )
                rng = make_rng(derive_seed(seed, "balance", family, key))
                shuffled = [candidates[int(i)] for i in rng.permutation(len(candidates))]
                shuffled.sort(key=_linked)  # stable: unlinked tasks are dropped first
                for task in shuffled[:excess]:
                    drops.append(task)
                    dropped.append(
                        (
                            task,
                            {
                                "task_id": task.task_id,
                                "answer": key,
                                "share_before": round(counts[key] / len(members), 4),
                                "cap": cap,
                                "family_n_before": len(members),
                            },
                        )
                    )
        drop_ids = {t.task_id for t in drops}
        after = Counter(task_key(t) for t in members if t.task_id not in drop_ids)
        n_after = sum(after.values())
        entry.update(
            n_after=n_after,
            dropped=len(drops),
            before={k: round(c / len(members), 4) for k, c in counts.most_common()},
            after={k: round(c / n_after, 4) for k, c in after.most_common()} if n_after else {},
        )
        report[family] = entry
    drop_ids = {t.task_id for t, _ in dropped}
    kept = [t for t in tasks if t.task_id not in drop_ids]
    return kept, dropped, report


def _manifest_row(task: TaskPackage) -> dict[str, Any]:
    row = ManifestRow(
        task_id=task.task_id,
        scene_id=task.scene_id,
        camera_id=task.camera_id,
        rig=task.rig,
        family=task.family,
        answer_type=task.answer_type,
        shortcut_conflict=task.shortcut_conflict,
        path=f"{TASKS_DIR}/{task.task_id}/{TASK_FILE}",
    )
    return row.model_dump(mode="json")


def _is_rebalance(row: dict[str, Any]) -> bool:
    return row.get("stage") == STAGE and row.get("reason") == REASON


def balance_run(
    run_dir: Path | str,
    seed: int = 0,
    caps: dict[str, float | None] | None = None,
    echo: Callable[[str], Any] = print,
) -> dict[str, Any]:
    lay = RunLayout(run_dir)
    if not lay.task_manifest.is_file():
        raise FileNotFoundError(f"{lay.task_manifest} not found: run vtf validate first")
    manifest = list(read_jsonl(lay.task_manifest))
    rejected = list(read_jsonl(lay.rejected)) if lay.rejected.is_file() else []
    previous = [r for r in rejected if _is_rebalance(r)]
    others = [r for r in rejected if not _is_rebalance(r)]

    # The pool: the current manifest plus every task an earlier rebalance dropped.
    pool: list[TaskPackage] = [read_task(lay.root / row["path"]) for row in manifest]
    seen = {t.task_id for t in pool}
    repooled = []
    for row in previous:
        task_id = (row.get("detail") or {}).get("task_id")
        path = lay.task_dir(task_id) / TASK_FILE if task_id else None
        if task_id and task_id not in seen and path is not None and path.is_file():
            repooled.append(read_task(path))
            seen.add(task_id)
    repooled.sort(key=lambda t: (t.scene_id, t.camera_id, t.task_id))
    pool += repooled

    caps = {**CAPS, **(caps or {})}
    kept, dropped, families = plan_rebalance(pool, seed, caps)
    prior_after = prior_baseline(kept)
    prior_before = prior_baseline(pool)
    blind = read_json(lay.baselines_dir / BLIND_FILE)

    # Task folders: G5 evidence on kept tasks, a failed G5 on dropped ones.
    kept_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for task in kept:
        kept_counts[task.family][task_key(task)] += 1
    for task in kept:
        counts = kept_counts[task.family]
        share = counts[task_key(task)] / sum(counts.values())
        evidence = g5_evidence(task, share, families[task.family]["cap"], prior_after, blind)
        updated = with_gate(task, STAGE, GateResult(passed=True, evidence=evidence))
        write_model(lay.task_dir(task.task_id) / TASK_FILE, updated)
    for task, detail in dropped:
        result = GateResult(passed=False, evidence={"reason": REASON, **detail})
        write_model(lay.task_dir(task.task_id) / TASK_FILE, with_gate(task, STAGE, result))

    kept_ids = {t.task_id for t in kept}
    rows = [row for row in manifest if row["task_id"] in kept_ids]
    rows += [_manifest_row(t) for t in repooled if t.task_id in kept_ids]
    new_rejections = [
        RejectedRow(
            scene_id=task.scene_id,
            camera_id=task.camera_id,
            family=task.family,
            template_id=task.template_id,
            question=task.question,
            stage=STAGE,
            reason=REASON,
            detail=detail,
        ).model_dump(mode="json")
        for task, detail in dropped
    ]
    write_jsonl(lay.task_manifest, rows)
    write_jsonl(lay.rejected, others + new_rejections)

    prior = {
        "note": "Prior-only baseline: always answer the family's most common answer (number "
        "answers: the family median, right within the task tolerance). 'after' is over the "
        "accepted (balanced) tasks; 'before_balance' over the validated pool.",
        "after": prior_after,
        "before_balance": prior_before,
    }
    write_json(lay.baselines_dir / PRIOR_FILE, prior)
    report = {
        "balance_version": BALANCE_VERSION,
        "seed": seed,
        "caps": caps,
        "pool": len(pool),
        "kept": len(kept),
        "dropped": len(dropped),
        "G5": {
            "run": len(pool),
            "pass": len(kept),
            "pass_rate": round(len(kept) / len(pool), 4) if pool else None,
        },
        "families": families,
    }
    write_json(lay.baselines_dir / BALANCE_FILE, report)
    echo(format_balance(report, prior))
    return report


def format_balance(report: dict[str, Any], prior: dict[str, Any]) -> str:
    fams = report["families"]
    width = max([len(f) for f in fams] + [len("family")])
    lines = [
        f"vtf balance: kept {report['kept']} of {report['pool']} tasks, "
        f"dropped {report['dropped']} (seed {report['seed']})",
        f"  {'family':<{width}}  {'kept':>9}  {'top answer':>10}  {'prior before':>12}  "
        f"{'prior after':>11}",
    ]
    for family, f in fams.items():
        before = prior["before_balance"]["families"].get(family, {}).get("accuracy")
        after = prior["after"]["families"].get(family, {}).get("accuracy")
        top = max(f["after"].values()) if f["after"] else 0.0
        note = "  (one answer value: cannot balance)" if f["single_answer"] else ""
        lines.append(
            f"  {family:<{width}}  {f['n_after']:>4}/{f['n_before']:<4}  {top:10.0%}  "
            f"{'-' if before is None else f'{before:.0%}':>12}  "
            f"{'-' if after is None else f'{after:.0%}':>11}{note}"
        )
    return "\n".join(lines)
