"""Message Batches path of the eval (brief 06): same body as a standard call, same replies.

Offline: a stub client stands in for the API. Task IDs come from the fixture's manifest.
"""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from vtf.core.io import read_jsonl, read_task
from vtf.core.layout import RunLayout
from vtf.eval import batch as batches
from vtf.eval.adapters import AdapterResponse, FatalAdapterError
from vtf.eval.adapters.anthropic import AnthropicAdapter
from vtf.eval.runner import _record

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "spec" / "examples" / "run_example"


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    dst = tmp_path / "run"
    shutil.copytree(FIXTURE, dst)
    return dst


def _message(model: str, text: str = '{"answer": 7}', thinking: int | None = 30):
    details = None if thinking is None else SimpleNamespace(thinking_tokens=thinking)
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        usage=SimpleNamespace(
            input_tokens=1200,
            output_tokens=40,
            output_tokens_details=details,
            service_tier="batch",
        ),
        stop_reason="end_turn",
        model=model,
    )


class StubBatches:
    def __init__(self) -> None:
        self.created: list[list[dict]] = []

    def create(self, requests):
        self.created.append(requests)
        return SimpleNamespace(id=f"msgbatch_{len(self.created)}")

    def retrieve(self, batch_id):
        counts = SimpleNamespace(processing=0, succeeded=1, errored=1, canceled=0, expired=0)
        return SimpleNamespace(
            processing_status="ended", request_counts=counts, created_at="t0", ended_at="t1"
        )

    def results(self, batch_id):
        requests = self.created[int(batch_id.split("_")[1]) - 1]
        out = []
        for i, r in enumerate(requests):
            if i == 0:
                result = SimpleNamespace(type="succeeded", message=_message(r["params"]["model"]))
            else:
                err = SimpleNamespace(type="overloaded_error", message="busy")
                result = SimpleNamespace(type="errored", error=SimpleNamespace(error=err))
            out.append(SimpleNamespace(custom_id=r["custom_id"], result=result))
        return out


class StubClient:
    default_headers = {"anthropic-version": "2026-10-21"}

    def __init__(self) -> None:
        self.batches = StubBatches()
        self.messages = SimpleNamespace(
            create=lambda **kw: _message(kw["model"]), batches=self.batches
        )


def _tasks(run: Path, n: int = 2):
    layout = RunLayout(run)
    rows = list(read_jsonl(layout.task_manifest))[:n]
    out = []
    for row in rows:
        task_file = layout.root / row["path"]
        task = read_task(task_file)
        out.append((task, task_file.parent / task.image))
    return out


def test_batch_params_are_the_standard_request(run_dir: Path) -> None:
    adapter = AnthropicAdapter(
        "claude-haiku-5-5", thinking="adaptive", effort="high", client=StubClient()
    )
    for task, image in _tasks(run_dir):
        req = batches.build_request(adapter, task, image)
        std = adapter.request(image, task.question, task.answer_type, True)
        assert req.custom_id == task.task_id
        assert json.dumps(req.params, sort_keys=True) == json.dumps(std, sort_keys=True)
        assert req.params["output_config"]["effort"] == "high"


def test_chunks_respect_both_caps() -> None:
    reqs = [batches.BatchRequest(custom_id=f"t{i}", params={}, size=40) for i in range(7)]
    sizes = [len(g) for g in batches.chunks(reqs, max_requests=3, max_bytes=1000)]
    assert sizes == [3, 3, 1]
    sizes = [len(g) for g in batches.chunks(reqs, max_requests=100, max_bytes=100)]
    assert sizes == [2, 2, 2, 1]


def test_collect_turns_results_into_replies(run_dir: Path) -> None:
    client = StubClient()
    adapter = AnthropicAdapter("claude-opus-5-5", effort="xhigh", max_tokens=64000, client=client)
    pairs = _tasks(run_dir)
    reqs = [batches.build_request(adapter, t, i) for t, i in pairs]
    batch_id = batches.submit(client, reqs)
    assert batches.status(client, batch_id)["processing_status"] == "ended"
    replies = batches.collect(client, batch_id, adapter)
    ok, failed = replies[pairs[0][0].task_id], replies[pairs[1][0].task_id]
    assert ok.error is None and ok.raw_text == '{"answer": 7}'
    assert ok.meta["via"] == "batch" and ok.meta["thinking_tokens"] == 30
    assert ok.meta["effort"] == "xhigh" and ok.meta["max_tokens"] == 64000
    assert ok.meta["model_id"] == "claude-opus-5-5" and ok.meta["api_version"] == "2026-10-21"
    assert failed.error and "overloaded_error" in failed.error

    record = _record(pairs[0][0], "claude-opus-5-5-xhigh", ok, False)
    assert record.thinking_tokens == 30 and record.via == "batch"
    assert record.effort == "xhigh" and record.max_tokens == 64000
    assert record.sdk and record.sdk.startswith("anthropic ")


def test_standard_reply_records_call_details(run_dir: Path) -> None:
    adapter = AnthropicAdapter("claude-haiku-5-5", effort="high", client=StubClient())
    task, image = _tasks(run_dir, 1)[0]
    reply = adapter.answer(image, task.question, task.answer_type)
    assert reply.meta["via"] == "standard" and reply.meta["thinking_tokens"] == 30
    old = AdapterResponse(raw_text='{"answer": 7}', stop_reason="end_turn", meta={})
    record = _record(task, "k", old, True, call_defaults=adapter.call_meta())
    assert record.model_id == "claude-haiku-5-5" and record.thinking_tokens is None


@pytest.mark.parametrize("model_id", ["claude-fable-5-2", "claude-mythos-5-2", "Claude-Fable-5"])
def test_fable_and_mythos_are_never_called(model_id: str) -> None:
    with pytest.raises(FatalAdapterError):
        AnthropicAdapter(model_id, client=StubClient())
