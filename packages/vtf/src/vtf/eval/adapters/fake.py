"""Deterministic offline adapters for tests and dry runs. No network, no cost.

fake-oracle       reads task.json next to the image and returns the true answer
fake-constant     returns the same answer for every question of a type
fake-unparseable  returns prose with no {"answer": ...}, to exercise parse failures
"""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from vtf.core.io import read_task
from vtf.core.layout import TASK_FILE
from vtf.core.program import AnswerType
from vtf.eval.adapters.base import Adapter, AdapterResponse


class FakeAdapter(Adapter):
    provider = "fake"

    def _reply(self, payload: Any) -> AdapterResponse:
        return AdapterResponse(
            raw_text=json.dumps(payload),
            latency_s=0.0,
            input_tokens=0,
            output_tokens=0,
            stop_reason="end_turn",
            meta={"output_mode": "fake"},
        )


class FakeOracle(FakeAdapter):
    model_id = "fake-oracle"

    def answer(self, image_path: Path, question: str, answer_type: AnswerType) -> AdapterResponse:
        task = read_task(Path(image_path).parent / TASK_FILE)
        return self._reply({"answer": task.answer})


class FakeConstant(FakeAdapter):
    model_id = "fake-constant"
    ANSWERS: dict[str, Any] = {"int": 3, "object": "red box", "set": [], "bool": True}

    def answer(self, image_path: Path, question: str, answer_type: AnswerType) -> AdapterResponse:
        return self._reply({"answer": self.ANSWERS[answer_type]})


class FakeUnparseable(FakeAdapter):
    model_id = "fake-unparseable"

    def answer(self, image_path: Path, question: str, answer_type: AnswerType) -> AdapterResponse:
        return replace(self._reply(None), raw_text="I cannot tell from this image.")


FAKES: dict[str, type[FakeAdapter]] = {
    cls.model_id: cls for cls in (FakeOracle, FakeConstant, FakeUnparseable)
}
