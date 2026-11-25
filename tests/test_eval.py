"""Eval harness: scorer, prompts, cache, runner and the Anthropic adapter (with a stub client).

Everything here is offline. Task IDs are read from the fixture's manifest, never hard-coded.
"""

import base64
import json
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from typer.testing import CliRunner

from vtf.cli import app
from vtf.core.io import read_jsonl
from vtf.core.layout import RunLayout
from vtf.core.task import EvalRecord
from vtf.eval.adapters import AdapterResponse, FatalAdapterError
from vtf.eval.adapters.anthropic import AnthropicAdapter
from vtf.eval.adapters.fake import FakeOracle
from vtf.eval.cache import EvalCache
from vtf.eval.config import resolve_model
from vtf.eval.prompt import (
    ANSWER_TYPES,
    PROMPT_FINGERPRINT,
    PROMPT_VERSION,
    prompt_fingerprint,
    response_schema,
    system_prompt,
)
from vtf.eval.runner import eval_run
from vtf.eval.scorer import score_response

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "spec" / "examples" / "run_example"


@pytest.fixture(autouse=True)
def cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "eval_cache.sqlite"
    monkeypatch.setenv("VTF_EVAL_CACHE", str(path))
    return path


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    dst = tmp_path / "run"
    shutil.copytree(FIXTURE, dst)
    return dst


def manifest_ids(run: Path) -> list[str]:
    return [row["task_id"] for row in read_jsonl(RunLayout(run).task_manifest)]


def results(run: Path, model: str) -> list[EvalRecord]:
    path = RunLayout(run).eval_dir(model) / "results.jsonl"
    return [EvalRecord.model_validate(row) for row in read_jsonl(path)]


class Counting(FakeOracle):
    """The oracle, counting its calls (thread-safe)."""

    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()

    def answer(self, image_path, question, answer_type):
        with self._lock:
            self.calls += 1
        return super().answer(image_path, question, answer_type)


class Failing(FakeOracle):
    def answer(self, image_path, question, answer_type):
        return AdapterResponse(raw_text="", error="HTTP 529 overloaded_error: Overloaded")


# --- scorer -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ['{"answer": 7}', '{"answer": "7"}', '{"answer": "seven"}', '{"answer": "Seven objects."}'],
)
def test_int_digits_and_words(raw: str) -> None:
    score = score_response("int", 7, raw)
    assert score.parse_ok and score.correct and score.parsed == 7


def test_int_wrong_and_tolerance() -> None:
    wrong = score_response("int", 7, '{"answer": 6}')
    assert wrong.parse_ok and not wrong.correct
    assert score_response("int", 7, '{"answer": 6}', tolerance=1).correct


@pytest.mark.parametrize(
    "raw", ['{"answer": "the red cube"}', '{"answer": "Red Box."}', '{"answer": "red block"}']
)
def test_object_synonyms(raw: str) -> None:
    score = score_response("object", "red box", raw)
    assert score.parse_ok and score.correct and score.parsed == "red box"


def test_object_wrong_is_parsed_but_wrong() -> None:
    score = score_response("object", "red box", '{"answer": "blue ball"}')
    assert score.parse_ok and not score.correct and score.parsed == "blue sphere"


@pytest.mark.parametrize(
    ("answer_type", "truth", "raw"),
    [
        ("int", 7, "I cannot tell from this image."),
        ("int", 7, '{"result": 7}'),
        ("int", 7, '{"answer": null}'),
        ("int", 7, '{"answer": true}'),
        ("int", 7, '{"answer": "7 or 8"}'),
        ("object", "red box", '{"answer": "the red or blue box"}'),
        ("object", "red box", '{"answer": "something red"}'),
        ("object", "red box", ""),
    ],
)
def test_unparseable_is_wrong_and_flagged(answer_type: str, truth: object, raw: str) -> None:
    score = score_response(answer_type, truth, raw)  # type: ignore[arg-type]
    assert not score.parse_ok and not score.correct and score.parsed is None


def test_answer_extracted_from_fences_and_prose() -> None:
    assert score_response("int", 3, '```json\n{"answer": 3}\n```').correct
    prose = 'I see three. {"answer": 2} Wait, recount. Final: {"answer": 3}'
    assert score_response("int", 3, prose).correct


def test_set_and_bool_ready() -> None:
    truth = ["blue cone", "red box"]
    assert score_response("set", truth, '{"answer": ["the red cube", "blue cone"]}').correct
    assert not score_response("set", truth, '{"answer": ["red box"]}').correct
    assert score_response("set", [], '{"answer": []}').correct
    assert score_response("bool", True, '{"answer": true}').correct
    assert score_response("bool", False, '{"answer": "no"}').correct
    assert not score_response("bool", True, '{"answer": "maybe"}').parse_ok


# --- prompts ----------------------------------------------------------------------------------


def test_prompt_version_tracks_prompt_text() -> None:
    assert prompt_fingerprint() == PROMPT_FINGERPRINT, (
        "prompt text or schema changed: bump PROMPT_VERSION and update PROMPT_FINGERPRINT"
    )


def test_prompts_and_schemas_per_answer_type() -> None:
    for answer_type in ANSWER_TYPES:
        assert '{"answer": ...}' in system_prompt(answer_type)
        schema = response_schema(answer_type)
        assert schema["required"] == ["answer"] and schema["additionalProperties"] is False


# --- cache ------------------------------------------------------------------------------------


def test_cache_roundtrip_and_key(tmp_path: Path) -> None:
    reply = AdapterResponse(
        raw_text='{"answer": 3}',
        latency_s=1.5,
        input_tokens=900,
        output_tokens=12,
        stop_reason="end_turn",
        meta={"output_mode": "text"},
    )
    with EvalCache(tmp_path / "c.sqlite") as cache:
        cache.put("t1", "m", PROMPT_VERSION, reply)
        assert cache.get("t1", "m", PROMPT_VERSION) == reply
        assert cache.get("t1", "m", "other-prompt") is None
        assert cache.get("t1", "other-model", PROMPT_VERSION) is None
        with pytest.raises(ValueError):
            cache.put("t2", "m", PROMPT_VERSION, AdapterResponse(raw_text="", error="HTTP 500"))


# --- runner -----------------------------------------------------------------------------------


def test_runner_end_to_end_and_cache_hit(run_dir: Path) -> None:
    oracle = Counting()
    summary = eval_run(run_dir, "fake-oracle", limit=3, adapter=oracle, echo=lambda _: None)
    assert oracle.calls == 3
    assert summary.n == 3 and summary.accuracy == 1.0 and summary.api_calls == 3
    assert set(summary.families) == {"count", "front_to_back"}
    records = results(run_dir, "fake-oracle")
    assert [r.task_id for r in records] == manifest_ids(run_dir)[:3]
    assert all(r.correct and r.parse_ok and not r.cached for r in records)
    saved = json.loads((RunLayout(run_dir).eval_dir("fake-oracle") / "summary.json").read_text())
    assert saved["families"]["count"] == {
        "n": 2,
        "correct": 2,
        "accuracy": 1.0,
        "parse_failures": 0,
    }

    rerun = Counting()
    summary = eval_run(run_dir, "fake-oracle", limit=3, adapter=rerun, echo=lambda _: None)
    assert rerun.calls == 0
    assert summary.cache_hits == 3 and summary.api_calls == 0 and summary.accuracy == 1.0
    assert all(r.cached for r in results(run_dir, "fake-oracle"))


def test_runner_limit_and_scores(run_dir: Path) -> None:
    summary = eval_run(run_dir, "fake-unparseable", limit=2, echo=lambda _: None)
    assert summary.n == 2 and summary.parse_failures == 2 and summary.accuracy == 0.0
    assert [r.task_id for r in results(run_dir, "fake-unparseable")] == manifest_ids(run_dir)[:2]
    summary = eval_run(run_dir, "fake-constant", echo=lambda _: None)
    assert summary.n == len(manifest_ids(run_dir)) and summary.parse_failures == 0


def test_failed_calls_are_not_cached(run_dir: Path) -> None:
    summary = eval_run(run_dir, "fake-oracle", adapter=Failing(), echo=lambda _: None)
    assert summary.errors == 3 and summary.accuracy == 0.0
    retry = Counting()
    summary = eval_run(run_dir, "fake-oracle", adapter=retry, echo=lambda _: None)
    assert retry.calls == 3 and summary.errors == 0 and summary.accuracy == 1.0


def test_cli_prints_accuracy_per_family(run_dir: Path) -> None:
    args = ["eval", str(run_dir), "--model", "fake-oracle", "--limit", "3"]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "count" in result.output and "front_to_back" in result.output
    assert "100.0%" in result.output
    result = CliRunner().invoke(app, args)
    assert "cache hits 3, api calls 0" in result.output


def test_model_resolution() -> None:
    spec = resolve_model("claude-sonnet-5-5")
    assert spec.provider == "anthropic" and spec.verified
    assert spec.price_usd_per_mtok is not None and spec.price_usd_per_mtok.input == 2.0
    assert spec.settings["effort"] == "high" and spec.settings["max_tokens"] == 16000
    with pytest.raises(ValueError, match="pinned"):
        resolve_model("anthropic-flagship")
    with pytest.raises(ValueError, match="unknown model"):
        resolve_model("not-a-model")


# --- Anthropic adapter, stub client (no network) ----------------------------------------------


def _bad_request(message: str) -> anthropic.BadRequestError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    return anthropic.BadRequestError(
        message, response=httpx2.Response(400, request=request), body=body
    )


class StubClient:
    """Records each messages.create call and answers {"answer": 7}."""

    default_headers = {"anthropic-version": "2026-10-21"}

    def __init__(self, reject_format: bool = False, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.reject_format = reject_format
        self.error = error
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.reject_format and "format" in kwargs.get("output_config", {}):
            raise _bad_request("output_config.format is not supported with image input")
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text='{"answer": 7}'),
            ],
            usage=SimpleNamespace(input_tokens=1200, output_tokens=40),
            stop_reason="end_turn",
            model=kwargs["model"],
            _request_id="req_stub",
        )


def _first_task(run: Path):
    layout = RunLayout(run)
    task_dir = layout.task_dir(manifest_ids(run)[0])
    return task_dir / "image.png"


def test_anthropic_request_shape(run_dir: Path) -> None:
    client = StubClient()
    adapter = AnthropicAdapter(
        "claude-sonnet-5-5", thinking="adaptive", effort="high", client=client
    )
    image = _first_task(run_dir)
    reply = adapter.answer(image, "How many objects are on the table?", "int")

    sent = client.calls[0]
    assert not {"temperature", "top_p", "top_k"} & set(sent)
    image_block, text_block = sent["messages"][0]["content"]
    assert image_block["type"] == "image" and image_block["source"]["type"] == "base64"
    assert image_block["source"]["media_type"] == "image/png"
    assert base64.standard_b64decode(image_block["source"]["data"]) == image.read_bytes()
    assert text_block == {"type": "text", "text": "How many objects are on the table?"}
    assert sent["system"] == system_prompt("int")
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"]["effort"] == "high"
    assert sent["output_config"]["format"] == {
        "type": "json_schema",
        "schema": response_schema("int"),
    }

    assert reply.raw_text == '{"answer": 7}' and reply.error is None
    assert (reply.input_tokens, reply.output_tokens) == (1200, 40)
    assert reply.meta["output_mode"] == "json_schema"
    assert adapter.api_version == "2026-10-21"


def test_anthropic_falls_back_to_plain_text(run_dir: Path) -> None:
    client = StubClient(reject_format=True)
    adapter = AnthropicAdapter("claude-sonnet-5-5", client=client)
    image = _first_task(run_dir)
    reply = adapter.answer(image, "How many?", "int")
    assert reply.error is None and reply.meta["output_mode"] == "text"
    assert len(client.calls) == 2 and "output_config" not in client.calls[1]
    assert adapter.settings["structured_output"] is False
    assert "not supported" in adapter.settings["structured_output_error"]
    adapter.answer(image, "How many?", "int")
    assert len(client.calls) == 3, "later calls go straight to plain text"


def test_anthropic_requires_project_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(FatalAdapterError, match="ANTHROPIC_API_KEY"):
        AnthropicAdapter("claude-sonnet-5-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    adapter = AnthropicAdapter("claude-sonnet-5-5")  # builds the client; sends nothing
    assert adapter.api_version == "2026-10-21"


def test_anthropic_errors(run_dir: Path) -> None:
    image = _first_task(run_dir)
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    overloaded = anthropic.OverloadedError(
        "Overloaded", response=httpx2.Response(529, request=request), body=None
    )
    reply = AnthropicAdapter("m", client=StubClient(error=overloaded)).answer(image, "q", "int")
    assert reply.error is not None and "529" in reply.error and reply.raw_text == ""

    denied = anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx2.Response(401, request=request), body=None
    )
    with pytest.raises(FatalAdapterError):
        AnthropicAdapter("m", client=StubClient(error=denied)).answer(image, "q", "int")


def test_runner_with_anthropic_stub(run_dir: Path) -> None:
    adapter = AnthropicAdapter("claude-sonnet-5-5", client=StubClient())
    summary = eval_run(run_dir, "claude-sonnet-5-5", adapter=adapter, echo=lambda _: None)
    assert summary.api_version == "2026-10-21" and summary.output_modes == {"json_schema": 3}
    assert summary.input_tokens == 3 * 1200 and summary.output_tokens == 3 * 40
    assert summary.estimated_cost_usd == pytest.approx((3600 * 2 + 120 * 10) / 1e6)
    counts = [r for r in results(run_dir, "claude-sonnet-5-5") if r.answer_type == "int"]
    assert all(r.parsed == 7 for r in counts)


def test_scale_stated_condition() -> None:
    """Eval condition "scale stated" (brief VTF-08): the grid sentence, or the reference
    object's exact size, in front of distance and free-space questions only."""
    from vtf.core.io import read_scene, read_task
    from vtf.core.task import GateResult
    from vtf.eval.scale import scale_prompt_version, stated_question
    from vtf.programs.templates import GRID_SCALE_SENTENCE

    run = Path(__file__).resolve().parents[1] / "spec" / "examples" / "run_example"
    task_file = next((run / "tasks").glob("*/task.json"))
    task = read_task(task_file)
    scene = read_scene(next((run / "scenes").glob("*/scene.json")))
    obj = scene.objects[0]

    def with_trace(family: str, step: dict):
        gates = {**task.gates, "G1": GateResult(passed=True, evidence={"trace": [step]})}
        return task.model_copy(update={"family": family, "gates": gates,
                                       "question": "How far apart are they?"})  # fmt: skip

    grid = with_trace("distance", {"step": "scale_in_frame", "kind": "grid", "grid_cell_m": 0.01})
    want = f"{GRID_SCALE_SENTENCE} How far apart are they?"
    assert stated_question(grid, lambda _: scene) == want
    ref = with_trace("free_space", {"step": "scale_in_frame", "kind": "reference",
                                    "object_id": obj.id, "asset": "proc_box_5cm"})  # fmt: skip
    ref_scene = scene.model_copy(update={"objects": [
        obj.model_copy(update={"shape": "proc_box_5cm", "dimensions": (0.05, 0.05, 0.05)}),
        *scene.objects[1:]]})  # fmt: skip
    q = stated_question(ref, lambda _: ref_scene)
    assert q.endswith("exactly 5 cm on each side. How far apart are they?")
    other = with_trace("count", {"step": "scale_in_frame", "kind": "grid", "grid_cell_m": 0.01})
    assert stated_question(other, lambda _: scene) == "How far apart are they?"
    assert scale_prompt_version().endswith("+scale.1")
