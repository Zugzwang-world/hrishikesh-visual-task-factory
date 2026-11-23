"""Fixed zero-shot prompts: one system prompt and one JSON schema per answer type.

The user turn is the image followed by the question text, nothing else. The answer vocabulary
(colour and size words) comes from vtf.core.vocab, the same place the question programs and the
scorer take it from. Object prompts explain the naming rule of spec/families.md (a noun, plus
colour and size only when needed) without an example answer.

PROMPT_VERSION is part of the cache key. Any change to the text or schemas below changes
prompt_fingerprint(); the test suite then fails until PROMPT_VERSION is bumped and
PROMPT_FINGERPRINT updated, so a cached reply is never reused for a different prompt.
"""

from typing import Any

from vtf.core.hashing import sha256_json
from vtf.core.program import AnswerType
from vtf.core.vocab import COLOURS, SIZE_WORDS

PROMPT_VERSION = "0.2.0"
PROMPT_FINGERPRINT = "49a723500d0045bce2dc606180829ceecc811725c7dab7a7537f7eaa007f93e5"

ANSWER_TYPES: tuple[AnswerType, ...] = ("int", "object", "set", "bool", "number")

# Naming guidance only, no example answer: a concrete example would anchor the model's guesses.
_NAMING = (
    "Name an object with a short everyday noun for what it is, adding its colour and the size "
    f"word {' or '.join(SIZE_WORDS)} only when they are needed to tell it apart from similar "
    f"objects in the image. Colours: {', '.join(COLOURS)}."
)

_INTRO = "You will see one image and one question about it. Look carefully at the image."
_OUTRO = 'Reply with only a JSON object of the form {"answer": ...} and nothing else.'

_INSTRUCTIONS: dict[str, str] = {
    "int": "The answer is a whole number. Give it as a JSON integer.",
    "object": (
        f"The answer is exactly one object, given as a JSON string. {_NAMING} If the answer is "
        "the table itself, give the string table."
    ),
    "set": (
        f"The answer is a list of objects, each given as a JSON string. {_NAMING} Give an empty "
        "list if there are none."
    ),
    "bool": "The answer is yes or no. Give it as a JSON boolean, true or false.",
    "number": (
        "The answer is a length in centimetres. Give your best estimate as a JSON number, "
        "without units."
    ),
}

_ANSWER_SCHEMAS: dict[str, dict[str, Any]] = {
    "int": {"type": "integer"},
    "object": {"type": "string"},
    "set": {"type": "array", "items": {"type": "string"}},
    "bool": {"type": "boolean"},
    "number": {"type": "number"},
}


def system_prompt(answer_type: AnswerType) -> str:
    return f"{_INTRO}\n{_INSTRUCTIONS[answer_type]}\n{_OUTRO}"


def response_schema(answer_type: AnswerType) -> dict[str, Any]:
    """JSON schema for structured outputs: {"answer": <typed value>}, nothing else."""
    return {
        "type": "object",
        "properties": {"answer": _ANSWER_SCHEMAS[answer_type]},
        "required": ["answer"],
        "additionalProperties": False,
    }


def user_text(question: str) -> str:
    """The text block that follows the image in the user turn."""
    return question


def prompt_fingerprint() -> str:
    """Hash of everything a model sees apart from the image and the question."""
    return sha256_json(
        {
            "system": {t: system_prompt(t) for t in ANSWER_TYPES},
            "schema": {t: response_schema(t) for t in ANSWER_TYPES},
            "user": user_text("{question}"),
        }
    )
