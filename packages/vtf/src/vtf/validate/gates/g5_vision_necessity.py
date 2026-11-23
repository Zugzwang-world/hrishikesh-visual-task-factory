"""G5 Vision necessity: balanced answers; prior-only and question-only baselines.

G5 is a dataset-level gate. It never decides an answer; it checks that the answers cannot be
guessed without the image:

- prior-only baseline: always answer the family's most common answer (for number answers, the
  family's median, right when within the task's tolerance). Computed per family and per
  family x rig over the accepted tasks, written to <run>/baselines/prior.json.
- balance: vtf.validate.balance drops tasks by seeded rejection sampling until no answer value
  dominates its family (bool 45 to 55%, any single int, object or set value at most 35%).
- question-only baseline: a model gets the question with no image (blind mode of the eval runner)
  on a stratified sample, default 15 tasks per family stratified by rig. Results go to
  <run>/eval/<model>__blind/ and the per-family accuracy to <run>/baselines/blind.json.

Every accepted task carries the G5 evidence: its answer's share in the family, the cap, the
family's prior answer and accuracy, the family x rig prior accuracy and the blind accuracy when
known. Models appear here only as a filter, never as the source of an answer.
"""

import json
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from vtf.core.io import read_task, write_model
from vtf.core.layout import RunLayout
from vtf.core.program import Answer
from vtf.core.task import GateResult, TaskPackage

PRIOR_FILE = "prior.json"
BLIND_FILE = "blind.json"
BALANCE_FILE = "balance.json"
DEFAULT_BLIND_MODEL = "claude-sonnet-5-5"
DEFAULT_PER_FAMILY = 15

# Largest share any one answer value may hold in its family (None: continuous, not capped).
CAPS: dict[str, float | None] = {
    "bool": 0.55,
    "int": 0.35,
    "object": 0.35,
    "set": 0.35,
    "number": None,
}
# A family is flagged when the blind model beats the prior by this much AND the lower end of its
# 95% bootstrap interval is above the prior (so 15-task samples do not raise noise flags).
BLIND_FLAG_MARGIN = 0.15


def answer_key(answer_type: str, answer: Answer) -> str:
    """A stable string for one answer value (sets sorted, numbers to 0.1)."""
    if answer_type == "set":
        return json.dumps(sorted(answer)) if isinstance(answer, list) else json.dumps([answer])
    if answer_type == "bool":
        return "true" if answer else "false"
    if answer_type == "number":
        return f"{float(answer):.1f}"  # type: ignore[arg-type]
    return str(answer)


def answer_label(answer_type: str, key: str) -> str:
    """Human-readable form of an answer_key: sets as 'a, b' or 'none', bools as yes/no."""
    if answer_type == "set":
        items = json.loads(key)
        return ", ".join(items) if items else "none"
    if answer_type == "bool":
        return "yes" if key == "true" else "no"
    return key


def task_key(task: TaskPackage) -> str:
    return answer_key(task.answer_type, task.answer)


def number_tolerance(task: TaskPackage) -> float:
    """The task's tolerance, else the distance family's rule max(2 cm, 10% of truth)."""
    if task.tolerance is not None:
        return task.tolerance
    return max(2.0, 0.1 * abs(float(task.answer)))  # type: ignore[arg-type]


def prior_stats(tasks: Sequence[TaskPackage], top: int = 8) -> dict[str, Any]:
    """Majority-answer baseline for one group of tasks (one family, or one family x rig)."""
    n = len(tasks)
    answer_type = tasks[0].answer_type
    counts = Counter(task_key(t) for t in tasks)
    distribution = {
        k: round(c / n, 4) for k, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    }
    if answer_type == "number":
        median = statistics.median(float(t.answer) for t in tasks)  # type: ignore[arg-type]
        correct = sum(abs(float(t.answer) - median) <= number_tolerance(t) for t in tasks)  # type: ignore[arg-type]
        prior_answer: Any = round(median, 1)
    else:
        prior_answer, correct = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "n": n,
        "answer_type": answer_type,
        "prior_answer": prior_answer,
        "correct": int(correct),
        "accuracy": round(correct / n, 4),
        "distinct_answers": len(counts),
        "distribution": distribution,
    }


def _groups(
    tasks: Iterable[TaskPackage], key: Callable[[TaskPackage], str]
) -> dict[str, list[TaskPackage]]:
    groups: dict[str, list[TaskPackage]] = defaultdict(list)
    for task in tasks:
        groups[key(task)].append(task)
    return dict(sorted(groups.items()))


def prior_baseline(tasks: Sequence[TaskPackage]) -> dict[str, Any]:
    """Prior-only baseline per family and per family x rig ('family|rig'), and overall."""
    families = {f: prior_stats(ts) for f, ts in _groups(tasks, lambda t: t.family).items()}
    by_rig = {
        k: prior_stats(ts) for k, ts in _groups(tasks, lambda t: f"{t.family}|{t.rig}").items()
    }
    correct = sum(s["correct"] for s in families.values())
    return {
        "n": len(tasks),
        "accuracy": round(correct / len(tasks), 4) if tasks else None,
        "families": families,
        "family_x_rig": by_rig,
    }


def effective_cap(cap: float | None, distinct: int) -> float | None:
    """A cap no group can meet with fewer than 1/cap distinct answers is relaxed to
    1/distinct + 0.05 (two values: 55%, the bool band); one value cannot be balanced at all."""
    if cap is None:
        return None
    if distinct * cap >= 1:
        return cap
    return min(1.0, 1.0 / distinct + 0.05)


def capped_counts(counts: dict[str, int], cap: float) -> dict[str, int]:
    """Largest kept count per answer value so that no value exceeds `cap` of the kept total."""
    total = sum(counts.values())
    while True:
        limit = max(1, math.floor(cap * total + 1e-9))
        kept = {k: min(c, limit) for k, c in counts.items()}
        new_total = sum(kept.values())
        if new_total == total:
            return kept
        total = new_total


def _run_tasks(run_dir: Path) -> list[TaskPackage]:
    from vtf.eval.runner import manifest_rows

    root = Path(run_dir)
    return [read_task(root / row.path) for row in manifest_rows(root)]


def read_json(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def write_json(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def g5_evidence(
    task: TaskPackage,
    share: float,
    cap: float | None,
    prior: dict[str, Any],
    blind: dict[str, Any] | None,
) -> dict[str, Any]:
    family = prior["families"].get(task.family, {})
    rig = prior["family_x_rig"].get(f"{task.family}|{task.rig}", {})
    evidence: dict[str, Any] = {
        "answer_share_in_family": round(share, 4),
        "cap": cap,
        "family_prior_answer": family.get("prior_answer"),
        "family_prior_accuracy": family.get("accuracy"),
        "rig_prior_accuracy": rig.get("accuracy"),
        "blind_model": None,
        "blind_accuracy": None,
        "blind_n": None,
    }
    fam_blind = (blind or {}).get("families", {}).get(task.family)
    if blind and fam_blind:
        evidence.update(
            blind_model=blind["model"], blind_accuracy=fam_blind["accuracy"], blind_n=fam_blind["n"]
        )
    return evidence


def with_gate(task: TaskPackage, gate: str, result: GateResult) -> TaskPackage:
    gates = dict(sorted({**task.gates, gate: result}.items()))
    return task.model_copy(update={"gates": gates})


def refresh_g5_evidence(run_dir: Path | str, caps: dict[str, float | None] | None = None) -> int:
    """Rewrite the G5 gate of every accepted task from the current manifest, prior.json and
    blind.json (when present). Returns the number of tasks written."""
    lay = RunLayout(run_dir)
    tasks = _run_tasks(lay.root)
    if not tasks:
        return 0
    caps = {**CAPS, **(caps or {})}
    prior = read_json(lay.baselines_dir / PRIOR_FILE) or {"after": prior_baseline(tasks)}
    blind = read_json(lay.baselines_dir / BLIND_FILE)
    for members in _groups(tasks, lambda t: t.family).values():
        counts = Counter(task_key(t) for t in members)
        cap = effective_cap(caps.get(members[0].answer_type), len(counts))
        for task in members:
            share = counts[task_key(task)] / len(members)
            evidence = g5_evidence(task, share, cap, prior["after"], blind)
            updated = with_gate(task, "G5", GateResult(passed=True, evidence=evidence))
            write_model(lay.task_dir(task.task_id) / "task.json", updated)
    return len(tasks)


# --- question-only baseline (blind mode) ------------------------------------------------------


def blind_baseline(
    run_dir: Path | str,
    model: str = DEFAULT_BLIND_MODEL,
    per_family: int = DEFAULT_PER_FAMILY,
    seed: int = 0,
    stratify: Sequence[str] = ("rig",),
    *,
    models_config: Path | str | None = None,
    adapter: Any = None,
    cache_path: Path | str | None = None,
    echo: Callable[[str], Any] = print,
) -> dict[str, Any]:
    """Ask `model` each sampled question with no image; compare with the prior per family."""
    from vtf.eval.runner import eval_run, manifest_rows, per_group_sample

    lay = RunLayout(run_dir)
    rows = manifest_rows(lay.root)
    picked = per_group_sample(rows, per_family, "family", stratify, seed)
    summary = eval_run(
        lay.root,
        model,
        models_config=models_config,
        task_ids=[r.task_id for r in picked],
        blind=True,
        adapter=adapter,
        cache_path=cache_path,
        echo=echo,
    )
    prior_file = read_json(lay.baselines_dir / PRIOR_FILE)
    prior = prior_file["after"] if prior_file else prior_baseline(_run_tasks(lay.root))
    families = {}
    for family, stats in summary.splits.get("family", {}).items():
        prior_acc = prior["families"].get(family, {}).get("accuracy")
        gap = None if prior_acc is None else round(stats.accuracy - prior_acc, 4)
        beats = (
            prior_acc is not None
            and stats.ci95_low is not None
            and stats.ci95_low > prior_acc
            and gap is not None
            and gap > BLIND_FLAG_MARGIN
        )
        families[family] = {
            **stats.model_dump(),
            "prior_accuracy": prior_acc,
            "blind_minus_prior": gap,
            "flag_blind_beats_prior": beats,
        }
    result = {
        "model": summary.model,
        "model_id": summary.model_id,
        "prompt_version": summary.prompt_version,
        "per_family": per_family,
        "stratify": ["family", *stratify],
        "seed": seed,
        "n": summary.n,
        "accuracy": summary.accuracy,
        "ci95": summary.ci95,
        "prior_accuracy": prior.get("accuracy"),
        "families": families,
        "task_ids": [r.task_id for r in picked],
    }
    write_json(lay.baselines_dir / BLIND_FILE, result)
    refresh_g5_evidence(lay.root)
    echo(format_blind(result))
    return result


def format_blind(result: dict[str, Any]) -> str:
    fams = result["families"]
    width = max([len(f) for f in fams] + [len("overall")])
    lines = [f"G5 question-only baseline: {result['model']} (no image), {result['n']} tasks"]
    lines.append(f"  {'family':<{width}}  {'blind':>12}  {'prior':>6}")
    for family, s in fams.items():
        prior = "-" if s["prior_accuracy"] is None else f"{s['prior_accuracy']:.0%}"
        flag = "  <- blind beats prior" if s["flag_blind_beats_prior"] else ""
        lines.append(
            f"  {family:<{width}}  {s['correct']:>3}/{s['n']:<3} {s['accuracy']:4.0%}  "
            f"{prior:>6}{flag}"
        )
    prior = result.get("prior_accuracy")
    lines.append(
        f"  {'overall':<{width}}  {result['accuracy']:12.0%}  "
        f"{'-' if prior is None else f'{prior:.0%}':>6}"
    )
    return "\n".join(lines)
