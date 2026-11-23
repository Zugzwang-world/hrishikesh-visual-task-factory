"""v0 answer vocabulary and the normaliser shared by question programs and the scorer.

Objects are named by colour plus shape ("red box"). The same functions turn a program's answer
and a model's free-text answer into one canonical form, so scoring is exact match after
normalising.

Note: these are workcell words living in core for v0; they move to the workcell domain pack in
stage 3 (see DECISIONS.md).
"""

import re
from collections.abc import Iterable

# Display colours in sRGB 0..255; `linear_rgb` gives the value written to SceneObject.rgb.
COLOURS_SRGB: dict[str, tuple[int, int, int]] = {
    "red": (200, 30, 30),
    "green": (40, 160, 60),
    "blue": (30, 80, 200),
    "yellow": (235, 205, 30),
    "orange": (240, 120, 20),
    "purple": (120, 50, 170),
    "white": (235, 235, 235),
    "black": (25, 25, 25),
}
COLOURS = tuple(COLOURS_SRGB)
SHAPES = ("box", "cylinder", "sphere", "cone")

COLOUR_SYNONYMS = {"violet": "purple"}
SHAPE_SYNONYMS = {
    "box": "box", "boxes": "box", "cube": "box", "cubes": "box", "block": "box",
    "blocks": "box", "cuboid": "box", "cuboids": "box",
    "cylinder": "cylinder", "cylinders": "cylinder", "can": "cylinder", "tube": "cylinder",
    "puck": "cylinder", "disc": "cylinder", "disk": "cylinder",
    "sphere": "sphere", "spheres": "sphere", "ball": "sphere", "balls": "sphere",
    "orb": "sphere",
    "cone": "cone", "cones": "cone",
}  # fmt: skip

NUMBER_WORDS = {
    w: i
    for i, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
}
TRUE_WORDS = {"yes", "true", "y"}
FALSE_WORDS = {"no", "false", "n"}


def srgb_to_linear(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_rgb(colour: str) -> tuple[float, float, float]:
    """Linear-light 0..1 base colour for a colour name, rounded to 4 digits."""
    return tuple(round(srgb_to_linear(v), 4) for v in COLOURS_SRGB[colour])  # type: ignore[return-value]


def object_name(colour: str | None, noun: str) -> str:
    """Canonical object name used in questions and answers, e.g. 'red box'."""
    return f"{colour} {noun}" if colour else noun


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z]+|\d+", str(text).lower())


def normalise_object(value: object) -> str | None:
    """'The red cube.' -> 'red box'. None unless exactly one colour and one shape are named."""
    if value is None:
        return None
    colours, shapes = set(), set()
    for tok in _tokens(str(value)):
        tok = COLOUR_SYNONYMS.get(tok, tok)
        if tok in COLOURS_SRGB:
            colours.add(tok)
        elif tok in SHAPE_SYNONYMS:
            shapes.add(SHAPE_SYNONYMS[tok])
    if len(colours) == 1 and len(shapes) == 1:
        return object_name(colours.pop(), shapes.pop())
    return None


def normalise_int(value: object) -> int | None:
    """7, '7', 'seven', '7 objects' -> 7. None if no single number is given."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    numbers = []
    for tok in _tokens(str(value)):
        if tok.isdigit():
            numbers.append(int(tok))
        elif tok in NUMBER_WORDS:
            numbers.append(NUMBER_WORDS[tok])
    return numbers[0] if len(set(numbers)) == 1 else None


def normalise_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    toks = set(_tokens(str(value)))
    if toks & TRUE_WORDS and not toks & FALSE_WORDS:
        return True
    if toks & FALSE_WORDS and not toks & TRUE_WORDS:
        return False
    return None


def normalise_set(value: object) -> list[str] | None:
    """A list, or a comma/'and'-separated string, of object names -> sorted canonical names."""
    if isinstance(value, str):
        items: Iterable[object] = [p for p in re.split(r",|;|\band\b", value) if p.strip()]
    elif isinstance(value, list | tuple | set):
        items = value
    else:
        return None
    names = [normalise_object(item) for item in items]
    if any(n is None for n in names):
        return None
    return sorted(set(names))  # type: ignore[arg-type]


def normalise(answer_type: str, value: object) -> object | None:
    """Dispatch on a TaskPackage answer_type: int, object, set or bool."""
    return {
        "int": normalise_int,
        "object": normalise_object,
        "set": normalise_set,
        "bool": normalise_bool,
    }[answer_type](value)
