"""Message Batches for the eval: the same request body as a standard call, at half the price.

Each request's params are exactly what AnthropicAdapter.request() gives messages.create, so a
batched reply and a standard reply to one task are interchangeable and share one cache entry
(task_id, model key, prompt version). A batch result becomes the AdapterResponse a standard call
would have returned, through the adapter's own response_from_message().

Batches are capped at 256 MB, and each request carries a base64 PNG of about 1 MB, so tasks go in
chunks of at most MAX_REQUESTS requests and MAX_BYTES bytes. Callers record every batch id in a
state file as soon as it is created, so a resumed run collects the batch instead of re-sending it.
"""

import json
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vtf.core.task import TaskPackage
from vtf.eval.adapters.anthropic import AnthropicAdapter
from vtf.eval.adapters.base import AdapterResponse

MAX_REQUESTS = 100
MAX_BYTES = 150_000_000  # the API's cap is 256 MB per batch
ENDED = "ended"


@dataclass(frozen=True)
class BatchRequest:
    custom_id: str
    params: dict[str, Any]
    size: int


def build_request(
    adapter: AnthropicAdapter, task: TaskPackage, image: Path, structured: bool | None = None
) -> BatchRequest:
    """The batch request for one task: custom_id is the task id, params the standard body."""
    params = adapter.request(
        image,
        task.question,
        task.answer_type,
        adapter.structured_output if structured is None else structured,
    )
    size = len(json.dumps(params))
    return BatchRequest(custom_id=task.task_id, params=params, size=size)


def chunks(
    requests: Iterable[BatchRequest],
    max_requests: int = MAX_REQUESTS,
    max_bytes: int = MAX_BYTES,
) -> Iterator[list[BatchRequest]]:
    """Consecutive groups under both caps (input order kept)."""
    group: list[BatchRequest] = []
    size = 0
    for request in requests:
        if group and (len(group) >= max_requests or size + request.size > max_bytes):
            yield group
            group, size = [], 0
        group.append(request)
        size += request.size
    if group:
        yield group


def submit(client: Any, requests: list[BatchRequest]) -> str:
    """Create one batch; returns its id."""
    batch = client.messages.batches.create(
        requests=[{"custom_id": r.custom_id, "params": r.params} for r in requests]
    )
    return batch.id


def status(client: Any, batch_id: str) -> dict[str, Any]:
    batch = client.messages.batches.retrieve(batch_id)
    counts = batch.request_counts
    return {
        "processing_status": batch.processing_status,
        "processing": counts.processing,
        "succeeded": counts.succeeded,
        "errored": counts.errored,
        "canceled": counts.canceled,
        "expired": counts.expired,
        "created_at": str(batch.created_at),
        "ended_at": str(batch.ended_at) if batch.ended_at else None,
    }


def wait(
    client: Any,
    batch_ids: Iterable[str],
    poll_s: float = 30.0,
    timeout_s: float | None = None,
    log: Callable[[str], Any] = print,
) -> bool:
    """Poll until every batch has ended (True) or the timeout passes (False)."""
    pending = set(batch_ids)
    start = time.monotonic()
    while pending:
        for batch_id in sorted(pending):
            info = status(client, batch_id)
            if info["processing_status"] == ENDED:
                pending.discard(batch_id)
                log(f"batch {batch_id} ended: {info}")
        if not pending:
            return True
        if timeout_s is not None and time.monotonic() - start > timeout_s:
            return False
        time.sleep(poll_s)
    return True


def collect(
    client: Any, batch_id: str, adapter: AnthropicAdapter, structured: bool | None = None
) -> dict[str, AdapterResponse]:
    """Every result of an ended batch, keyed by custom_id (the task id). Errored, canceled and
    expired requests come back with `error` set, so they are not cached and can be re-sent."""
    structured = adapter.structured_output if structured is None else structured
    out: dict[str, AdapterResponse] = {}
    for item in client.messages.batches.results(batch_id):
        result = item.result
        if result.type == "succeeded":
            out[item.custom_id] = adapter.response_from_message(
                result.message,
                structured=structured,
                latency_s=None,
                via="batch",
                extra_meta={"batch_id": batch_id},
            )
        elif result.type == "errored":
            err = result.error
            inner = getattr(err, "error", err)
            out[item.custom_id] = AdapterResponse(
                raw_text="",
                error=f"batch errored: {getattr(inner, 'type', '')}: "
                f"{getattr(inner, 'message', inner)}",
                meta={"batch_id": batch_id, "via": "batch"},
            )
        else:
            out[item.custom_id] = AdapterResponse(
                raw_text="",
                error=f"batch {result.type}",
                meta={"batch_id": batch_id, "via": "batch"},
            )
    return out
