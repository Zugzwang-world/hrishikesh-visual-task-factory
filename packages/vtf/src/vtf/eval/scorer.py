"""Typed scoring: pull {"answer": ...} out of a model's raw reply, normalise it with the shared
normaliser (vtf.core.vocab) and compare it with the program's answer.

- int and bool: exact after normalising (int with an optional tolerance).
- number (centimetres): |answer - truth| <= tolerance; an explicit unit (mm, m) converts.
- object and set on v0.2 tasks (with `scene_objects`, spec/families.md): the answer's colour,
  size and noun words are resolved against the scene's objects; an object answer is right when
  they pick out exactly the truth object (so "the red mug" is right when the truth is "mug" and
  only one mug exists); "table" answers a support question; a set is right when every item
  resolves to one object and the resolved set equals the truth ("none" or [] is empty).
- object and set on v0.1 tasks (no scene_objects): the v0 canonical "<colour> <shape>" names.

A reply with no {"answer": ...} object, or whose answer the normaliser cannot read (no single
number, no naming words, conflicting colours, ...), is unparseable: parse_ok is False and it
counts as wrong.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from vtf.core.program import Answer, AnswerType
from vtf.core.vocab import (
    NONE_ANSWER,
    TABLE_ANSWER,
    Nameable,
    Resolution,
    full_name,
    normalise,
    normalise_number,
    resolve_object,
    split_items,
    unique_name,
)

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


def _words_text(r: Resolution) -> str:
    w = r.words
    return " ".join([*w.sizes, *w.colours, *w.nouns]) if w else ""


def _resolved_name(r: Resolution, objects: Sequence[Nameable]) -> str:
    if r.kind == "object":
        obj = next(o for o in objects if o.id == r.ids[0])
        return unique_name(obj, objects) or full_name(obj)
    if r.kind == "table":
        return TABLE_ANSWER
    if r.kind == "none":
        return NONE_ANSWER
    return _words_text(r)


def _truth_ids(truth: object, objects: Sequence[Nameable]) -> tuple[str, ...]:
    r = resolve_object(truth, objects)
    if r.kind == "table":
        return (TABLE_ANSWER,)
    if r.kind != "object":
        raise ValueError(f"truth {truth!r} does not name exactly one scene object")
    return r.ids


def _score_object(truth: Answer, value: object, objects: Sequence[Nameable]) -> Score:
    r = resolve_object(value, objects)
    if r.kind == "unparseable":
        return Score(parsed=None, parse_ok=False, correct=False)
    expected = _truth_ids(truth, objects)
    got = (TABLE_ANSWER,) if r.kind == "table" else (r.ids if r.kind == "object" else None)
    return Score(parsed=_resolved_name(r, objects), parse_ok=True, correct=got == expected)


def _score_set(truth: Answer, value: object, objects: Sequence[Nameable]) -> Score:
    items = split_items(value)
    if items is None:
        return Score(parsed=None, parse_ok=False, correct=False)
    expected = set()
    for name in truth if isinstance(truth, list) else []:
        expected.update(_truth_ids(name, objects))
    resolved, names, ok = set(), [], True
    for item in items:
        r = resolve_object(item, objects)
        if r.kind == "unparseable":
            return Score(parsed=None, parse_ok=False, correct=False)
        if r.kind == "none":
            continue
        names.append(_resolved_name(r, objects))
        if r.kind == "object":
            resolved.update(r.ids)
        else:
            ok = False
    return Score(parsed=sorted(set(names)), parse_ok=True, correct=ok and resolved == expected)


def score_value(
    answer_type: AnswerType,
    truth: Answer,
    value: object,
    tolerance: float | None = None,
    scene_objects: Sequence[Nameable] | None = None,
) -> Score:
    """Score an already-extracted answer value against the truth."""
    if value is None:
        return Score(parsed=None, parse_ok=False, correct=False)
    if scene_objects and answer_type == "object":
        return _score_object(truth, value, scene_objects)
    if scene_objects and answer_type == "set":
        return _score_set(truth, value, scene_objects)
    if answer_type == "number":
        parsed_num = normalise_number(value)
        if parsed_num is None:
            return Score(parsed=None, parse_ok=False, correct=False)
        expected_num = float(truth)  # type: ignore[arg-type]
        correct = abs(parsed_num - expected_num) <= (tolerance or 0.0) + 1e-9
        return Score(parsed=round(parsed_num, 4), parse_ok=True, correct=correct)
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
    answer_type: AnswerType,
    truth: Answer,
    raw_text: str,
    tolerance: float | None = None,
    scene_objects: Sequence[Nameable] | None = None,
) -> Score:
    """Score a model's raw reply. Pass the task's `scene_objects` for v0.2 tasks."""
    value = extract_answer(raw_text)
    if value is _MISSING:
        return Score(parsed=None, parse_ok=False, correct=False)
    return score_value(answer_type, truth, value, tolerance, scene_objects)
