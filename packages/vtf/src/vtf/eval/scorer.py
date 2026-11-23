"""Typed scoring: pull {"answer": ...} out of a model's raw reply, normalise it with the shared
normaliser (vtf.core.vocab) and compare it with the program's answer.

A reply with no {"answer": ...} object, or whose answer the normaliser cannot read (no single
number, not exactly one colour and one shape, ...), is unparseable: parse_ok is False and it
counts as wrong.
"""

import json
import re
from dataclasses import dataclass

from vtf.core.program import Answer, AnswerType
from vtf.core.vocab import normalise

_MISSING = object()
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


@dataclass(frozen=True)
class Score:
    parsed: Answer | None
    """The model's answer in canonical form (None when unparseable)."""
    parse_ok: bool
    correct: bool


def extract_answer(raw_text: str) -> object:
    """The value of the "answer" key in the reply, or _MISSING.

    Accepts the bare JSON object (structured outputs), a fenced code block, or a JSON object
    embedded in prose (the plain-text fallback). With several objects, the last one wins.
    """
    text = _FENCE.sub("", raw_text.strip())
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "answer" in data:
            return data["answer"]
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    found: object = _MISSING
    for match in re.finditer(r"\{", raw_text):
        try:
            data, _ = decoder.raw_decode(raw_text, match.start())
        except ValueError:
            continue
        if isinstance(data, dict) and "answer" in data:
            found = data["answer"]
    return found


def _canonical(answer_type: AnswerType, value: object) -> Answer | None:
    if value is None:
        return None
    return normalise(answer_type, value)  # type: ignore[return-value]


def score_value(
    answer_type: AnswerType, truth: Answer, value: object, tolerance: float | None = None
) -> Score:
    """Score an already-extracted answer value against the truth."""
    parsed = _canonical(answer_type, value)
    if parsed is None:
        return Score(parsed=None, parse_ok=False, correct=False)
    expected = _canonical(answer_type, truth)
    if expected is None:
        raise ValueError(f"truth {truth!r} is not a valid {answer_type} answer")
    if answer_type == "int" and tolerance is not None:
        correct = abs(parsed - expected) <= tolerance  # type: ignore[operator]
    else:
        correct = parsed == expected
    return Score(parsed=parsed, parse_ok=True, correct=bool(correct))


def score_response(
    answer_type: AnswerType, truth: Answer, raw_text: str, tolerance: float | None = None
) -> Score:
    """Score a model's raw reply."""
    value = extract_answer(raw_text)
    if value is _MISSING:
        return Score(parsed=None, parse_ok=False, correct=False)
    return score_value(answer_type, truth, value, tolerance)
