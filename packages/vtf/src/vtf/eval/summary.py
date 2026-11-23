"""summary.json: accuracy per family for one model on one run, plus tokens and cost."""

from collections import Counter, defaultdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vtf.core.task import EvalRecord
from vtf.eval.config import Price

SUMMARY_VERSION = "0.1.0"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FamilyStats(_Model):
    n: int
    correct: int
    accuracy: float
    parse_failures: int


class EvalSummary(_Model):
    summary_version: str = SUMMARY_VERSION
    model: str = Field(description="The --model key results are filed and cached under.")
    model_id: str
    provider: str
    api_version: str | None
    prompt_version: str
    settings: dict[str, Any] = Field(description="Request and reasoning settings used.")
    n: int
    correct: int
    accuracy: float
    families: dict[str, FamilyStats]
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
        n=len(records),
        correct=correct,
        accuracy=_accuracy(correct, len(records)),
        families=families,
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


def format_summary(summary: EvalSummary, run_dir: str) -> str:
    """Plain-text report printed by vtf eval."""
    width = max([len(f) for f in summary.families] + [len("overall")])
    lines = [
        f"vtf eval: {summary.model} on {run_dir} "
        f"({summary.n} tasks, prompt {summary.prompt_version}, api {summary.api_version or '-'})"
    ]
    for family, stats in summary.families.items():
        lines.append(
            f"  {family:<{width}}  {stats.correct:>4}/{stats.n:<4} {stats.accuracy:7.1%}"
            + (f"  ({stats.parse_failures} unparseable)" if stats.parse_failures else "")
        )
    lines.append(
        f"  {'overall':<{width}}  {summary.correct:>4}/{summary.n:<4} {summary.accuracy:7.1%}"
    )
    cost = "n/a" if summary.estimated_cost_usd is None else f"${summary.estimated_cost_usd:.4f}"
    lines.append(
        f"  parse failures {summary.parse_failures}, errors {summary.errors}, "
        f"cache hits {summary.cache_hits}, api calls {summary.api_calls}, "
        f"tokens {summary.input_tokens} in / {summary.output_tokens} out, est. cost {cost}"
    )
    return "\n".join(lines)
