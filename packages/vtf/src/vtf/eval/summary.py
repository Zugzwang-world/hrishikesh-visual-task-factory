"""summary.json: accuracy per family for one model on one run, plus tokens and cost.

Accuracy is also split by rig, by shortcut_conflict (true, false, n/a) and by family x
shortcut_conflict, each with its count and a 95% bootstrap interval (seeded, so the same records
always give the same interval).
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from vtf.core.rng import derive_seed
from vtf.core.task import EvalRecord
from vtf.eval.config import Price

SUMMARY_VERSION = "0.2.0"
BOOTSTRAP_RESAMPLES = 2000
SPLITS = ("family", "rig", "shortcut_conflict", "family_x_shortcut_conflict")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FamilyStats(_Model):
    n: int
    correct: int
    accuracy: float
    parse_failures: int


class SplitStats(_Model):
    n: int
    correct: int
    accuracy: float
    ci95_low: float | None = Field(description="2.5th percentile of the bootstrap accuracy.")
    ci95_high: float | None = Field(description="97.5th percentile of the bootstrap accuracy.")
    parse_failures: int


class EvalSummary(_Model):
    summary_version: str = SUMMARY_VERSION
    model: str = Field(description="The --model key results are filed and cached under.")
    model_id: str
    provider: str
    api_version: str | None
    prompt_version: str
    settings: dict[str, Any] = Field(description="Request and reasoning settings used.")
    with_image: bool = Field(default=True, description="False for blind (question-only) runs.")
    n: int
    correct: int
    accuracy: float
    ci95: tuple[float, float] | None = Field(
        default=None, description="95% bootstrap interval of the overall accuracy."
    )
    families: dict[str, FamilyStats]
    splits: dict[str, dict[str, SplitStats]] = Field(
        default_factory=dict,
        description="Accuracy by family, rig, shortcut_conflict and family_x_shortcut_conflict "
        "(keys 'family|true', 'family|false', 'family|n/a'), each with a 95% bootstrap interval.",
    )
    parse_failures: int = Field(description="Replies with no readable answer (counted wrong).")
    errors: int = Field(description="Tasks with no reply or a non-final stop (counted wrong).")
    cache_hits: int
    api_calls: int
    output_modes: dict[str, int] = Field(description="json_schema, text or fake: replies each.")
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float | None = Field(
        description="All n tasks at list price, cached replies included (null if no price)."
    )
    latency_s_mean: float | None


def _accuracy(correct: int, n: int) -> float:
    return round(correct / n, 4) if n else 0.0


def bootstrap_ci(
    correct: int, n: int, resamples: int = BOOTSTRAP_RESAMPLES
) -> tuple[float, float] | None:
    """95% percentile bootstrap interval of an accuracy (correct out of n), seeded.

    Resampling n right/wrong outcomes with replacement is a Binomial(n, correct/n) draw, so the
    bootstrap draws from that binomial directly.
    """
    if n <= 0:
        return None
    rng = np.random.default_rng(derive_seed("bootstrap", correct, n, resamples))
    draws = rng.binomial(n, correct / n, size=resamples) / n
    low, high = np.percentile(draws, [2.5, 97.5])
    return round(float(low), 4), round(float(high), 4)


def conflict_label(value: bool | None) -> str:
    return "n/a" if value is None else ("true" if value else "false")


SPLIT_KEYS: dict[str, Callable[[EvalRecord], str]] = {
    "family": lambda r: r.family,
    "rig": lambda r: r.rig or "unknown",
    "shortcut_conflict": lambda r: conflict_label(r.shortcut_conflict),
    "family_x_shortcut_conflict": lambda r: f"{r.family}|{conflict_label(r.shortcut_conflict)}",
}


def split_stats(
    records: Iterable[EvalRecord], key: Callable[[EvalRecord], str]
) -> dict[str, SplitStats]:
    groups: dict[str, list[EvalRecord]] = defaultdict(list)
    for record in records:
        groups[key(record)].append(record)
    out = {}
    for name, rows in sorted(groups.items()):
        correct = sum(r.correct for r in rows)
        ci = bootstrap_ci(correct, len(rows))
        out[name] = SplitStats(
            n=len(rows),
            correct=correct,
            accuracy=_accuracy(correct, len(rows)),
            ci95_low=ci[0] if ci else None,
            ci95_high=ci[1] if ci else None,
            parse_failures=sum(not r.parse_ok for r in rows),
        )
    return out


def all_splits(records: list[EvalRecord]) -> dict[str, dict[str, SplitStats]]:
    return {name: split_stats(records, SPLIT_KEYS[name]) for name in SPLITS}


def summarise(
    records: list[EvalRecord],
    *,
    model: str,
    model_id: str,
    provider: str,
    api_version: str | None,
    prompt_version: str,
    settings: dict[str, Any],
    price: Price | None,
    output_modes: Counter[str],
    with_image: bool = True,
) -> EvalSummary:
    by_family: dict[str, list[EvalRecord]] = defaultdict(list)
    for record in records:
        by_family[record.family].append(record)
    families = {
        family: FamilyStats(
            n=len(rows),
            correct=sum(r.correct for r in rows),
            accuracy=_accuracy(sum(r.correct for r in rows), len(rows)),
            parse_failures=sum(not r.parse_ok for r in rows),
        )
        for family, rows in sorted(by_family.items())
    }
    correct = sum(r.correct for r in records)
    input_tokens = sum(r.input_tokens or 0 for r in records)
    output_tokens = sum(r.output_tokens or 0 for r in records)
    latencies = [r.latency_s for r in records if r.latency_s is not None and not r.cached]
    cost = None
    if price is not None:
        cost = round((input_tokens * price.input + output_tokens * price.output) / 1e6, 6)
    return EvalSummary(
        model=model,
        model_id=model_id,
        provider=provider,
        api_version=api_version,
        prompt_version=prompt_version,
        settings=settings,
        with_image=with_image,
        n=len(records),
        correct=correct,
        accuracy=_accuracy(correct, len(records)),
        ci95=bootstrap_ci(correct, len(records)),
        families=families,
        splits=all_splits(records),
        parse_failures=sum(not r.parse_ok for r in records),
        errors=sum(r.error is not None for r in records),
        cache_hits=sum(r.cached for r in records),
        api_calls=sum(not r.cached for r in records),
        output_modes=dict(sorted(output_modes.items())),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=cost,
        latency_s_mean=round(sum(latencies) / len(latencies), 3) if latencies else None,
    )


def _ci_text(stats: SplitStats | None) -> str:
    if stats is None or stats.ci95_low is None or stats.ci95_high is None:
        return ""
    return f"  [{stats.ci95_low:.0%}, {stats.ci95_high:.0%}]"


def format_summary(summary: EvalSummary, run_dir: str) -> str:
    """Plain-text report printed by vtf eval."""
    extra = {
        f"{label} {key}": stats
        for split, label in (("rig", "rig"), ("shortcut_conflict", "conflict"))
        for key, stats in summary.splits.get(split, {}).items()
        if len(summary.splits.get(split, {})) > 1
    }
    width = max([len(f) for f in [*summary.families, *extra]] + [len("overall")])
    mode = "" if summary.with_image else ", blind: no image"
    lines = [
        f"vtf eval: {summary.model} on {run_dir} "
        f"({summary.n} tasks, prompt {summary.prompt_version}, api {summary.api_version or '-'}"
        f"{mode})"
    ]
    family_splits = summary.splits.get("family", {})
    for family, stats in summary.families.items():
        lines.append(
            f"  {family:<{width}}  {stats.correct:>4}/{stats.n:<4} {stats.accuracy:7.1%}"
            f"{_ci_text(family_splits.get(family))}"
            + (f"  ({stats.parse_failures} unparseable)" if stats.parse_failures else "")
        )
    overall_ci = f"  [{summary.ci95[0]:.0%}, {summary.ci95[1]:.0%}]" if summary.ci95 else ""
    lines.append(
        f"  {'overall':<{width}}  {summary.correct:>4}/{summary.n:<4} {summary.accuracy:7.1%}"
        f"{overall_ci}"
    )
    for name, stats in extra.items():
        lines.append(
            f"  {name:<{width}}  {stats.correct:>4}/{stats.n:<4} {stats.accuracy:7.1%}"
            f"{_ci_text(stats)}"
        )
    cost = "n/a" if summary.estimated_cost_usd is None else f"${summary.estimated_cost_usd:.4f}"
    lines.append(
        f"  parse failures {summary.parse_failures}, errors {summary.errors}, "
        f"cache hits {summary.cache_hits}, api calls {summary.api_calls}, "
        f"tokens {summary.input_tokens} in / {summary.output_tokens} out, est. cost {cost}"
    )
    return "\n".join(lines)
