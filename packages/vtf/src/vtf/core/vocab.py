"""Answer vocabulary, the naming rule and the normalisers shared by programs and the scorer.

v0 (stage 2): objects were named by colour plus shape ("red box"); `normalise_object` turns a
free-text answer into that canonical form, and v0.1 tasks are still scored that way.

v0.2 (stage 3, spec/families.md): an object is named by the first of `category`,
`colour category`, `size category`, `size colour category` that picks it out alone among the
scene's objects (`unique_name`). A free-text answer is read into its colour, size and noun words
(`parse_name_words`) and resolved against the scene's objects (`resolve_object`): it is right when
those words match exactly one object and that object is the truth. The same resolver decides
uniqueness for the programs, so a name a program writes always resolves back to its object.

Category nouns and plurals come from the asset manifest (vtf.core.assets), plus the primitive
shape words and their synonyms.

Note: these are workcell words living in core; they move to the workcell domain pack later (see
DECISIONS.md).
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

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
    "brown": (120, 72, 36),
    "grey": (128, 128, 128),
    "pink": (240, 130, 175),
}
COLOURS = tuple(COLOURS_SRGB)
SHAPES = ("box", "cylinder", "sphere", "cone")
SIZE_WORDS = ("small", "large")

COLOUR_SYNONYMS = {"violet": "purple", "gray": "grey"}
SIZE_SYNONYMS = {
    "small": "small", "smaller": "small", "smallest": "small", "little": "small", "tiny": "small",
    "large": "large", "larger": "large", "largest": "large", "big": "large", "bigger": "large",
    "biggest": "large",
}  # fmt: skip
SHAPE_SYNONYMS = {
    "box": "box", "boxes": "box", "cube": "box", "cubes": "box", "block": "box",
    "blocks": "box", "cuboid": "box", "cuboids": "box",
    "cylinder": "cylinder", "cylinders": "cylinder", "can": "cylinder", "tube": "cylinder",
    "puck": "cylinder", "disc": "cylinder", "disk": "cylinder",
    "sphere": "sphere", "spheres": "sphere", "ball": "sphere", "balls": "sphere",
    "orb": "sphere",
    "cone": "cone", "cones": "cone",
}  # fmt: skip
# v0.2: interchangeable nouns. A noun matches a category of its class only when the scene has no
# category spelled exactly like the noun (so "cube" means the cube when there is one).
NOUN_CLASSES: tuple[frozenset[str], ...] = (
    frozenset({"box", "cube", "block", "cuboid"}),
    frozenset({"sphere", "ball", "orb"}),
    frozenset({"cylinder", "can", "tube", "puck", "disc", "disk"}),
    frozenset({"cone"}),
)
TABLE_WORDS = frozenset({"table", "tabletop", "desk"})
NONE_WORDS = frozenset({"none", "nothing", "empty"})
TABLE_ANSWER = "table"
NONE_ANSWER = "none"

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
    """v0 canonical object name, e.g. 'red box'."""
    return f"{colour} {noun}" if colour else noun


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z]+|\d+", str(text).lower())


# ---------------------------------------------------------------------------------------------
# nouns: asset categories, plurals and primitive shape words


def default_plural(noun: str) -> str:
    words = noun.split(" ")
    last = words[-1]
    if re.search(r"(s|x|z|ch|sh)$", last):
        last += "es"
    elif re.search(r"[^aeiou]y$", last):
        last = last[:-1] + "ies"
    else:
        last += "s"
    return " ".join([*words[:-1], last])


@lru_cache(maxsize=1)
def _manifest_nouns() -> tuple[tuple[str, str], ...]:
    """(category, plural) for every asset in the asset manifest; empty if it cannot be read."""
    try:
        from vtf.core.assets import load_asset_manifest

        manifest = load_asset_manifest()
    except (OSError, ValueError):
        return ()
    out = {}
    for a in manifest.assets:
        out.setdefault(a.category, a.plural or default_plural(a.category))
    return tuple(sorted(out.items()))


def plural(category: str) -> str:
    """Plural noun for a category: the asset manifest's, else the English default."""
    return dict(_manifest_nouns()).get(category) or default_plural(category)


@lru_cache(maxsize=1)
def noun_phrases() -> dict[str, str]:
    """Every known noun phrase, singular or plural, -> its singular form."""
    out: dict[str, str] = {}
    singulars = set(SHAPES) | {w for cls in NOUN_CLASSES for w in cls}
    for noun in sorted(singulars):
        out[noun] = noun
        out.setdefault(default_plural(noun), noun)
    for category, plural_form in _manifest_nouns():
        out[category] = category
        out[plural_form] = category
        out.setdefault(default_plural(category), category)
    return out


def category_words() -> tuple[str, ...]:
    """Every category noun the factory can name: asset categories and primitive shapes."""
    return tuple(sorted(set(SHAPES) | {c for c, _ in _manifest_nouns()}))


def noun_class(noun: str) -> frozenset[str]:
    for cls in NOUN_CLASSES:
        if noun in cls:
            return cls
    return frozenset({noun})


# ---------------------------------------------------------------------------------------------
# v0.2: parsing an answer into naming words, and resolving it against the scene's objects


class Nameable(Protocol):
    """Anything with naming words: a SceneObject or a task's ObjectRef."""

    id: str
    category: str
    color_name: str | None
    size_word: str | None


@dataclass(frozen=True)
class NameWords:
    colours: tuple[str, ...]
    sizes: tuple[str, ...]
    nouns: tuple[str, ...]  # singular nouns as written (synonyms not yet resolved)
    table: bool = False
    none: bool = False

    @property
    def empty(self) -> bool:
        return not (self.colours or self.sizes or self.nouns or self.table or self.none)

    @property
    def single(self) -> bool:
        """At most one colour, one size and one noun: can name a single object."""
        return len(self.colours) <= 1 and len(self.sizes) <= 1 and len(self.nouns) <= 1


def parse_name_words(text: object, extra_nouns: Iterable[str] = ()) -> NameWords:
    """Colour, size and noun words in a free-text name ('the large red mug').

    Multi-word nouns from the manifest are matched longest first. A word that is both a colour
    and a noun (an orange) is a noun when no other noun is present, else a colour.
    """
    phrases = dict(noun_phrases())
    for noun in extra_nouns:
        phrases.setdefault(noun, noun)
        phrases.setdefault(default_plural(noun), noun)
    max_len = max((len(p.split()) for p in phrases), default=1)
    toks = _tokens(str(text))
    colours: list[str] = []
    sizes: list[str] = []
    nouns: list[str] = []
    both: list[str] = []
    table = none = False
    i = 0
    while i < len(toks):
        for n in range(min(max_len, len(toks) - i), 1, -1):
            phrase = " ".join(toks[i : i + n])
            if phrase in phrases:
                nouns.append(phrases[phrase])
                i += n
                break
        else:
            tok = toks[i]
            colour = COLOUR_SYNONYMS.get(tok, tok)
            is_colour = colour in COLOURS_SRGB
            is_noun = tok in phrases
            if is_colour and is_noun:
                both.append(tok)
            elif is_colour:
                colours.append(colour)
            elif is_noun:
                nouns.append(phrases[tok])
            elif tok in SIZE_SYNONYMS:
                sizes.append(SIZE_SYNONYMS[tok])
            elif tok in TABLE_WORDS:
                table = True
            elif tok in NONE_WORDS:
                none = True
            i += 1
    if both:
        if nouns:
            colours += [COLOUR_SYNONYMS.get(t, t) for t in both]
        else:
            nouns.append(phrases[both[-1]])
            colours += [COLOUR_SYNONYMS.get(t, t) for t in both[:-1]]
    uniq = lambda xs: tuple(sorted(set(xs)))  # noqa: E731
    return NameWords(uniq(colours), uniq(sizes), uniq(nouns), table, none)


def noun_matches(noun: str, category: str, scene_categories: set[str]) -> bool:
    """Does a noun from an answer name this category? Exact spelling wins over synonyms."""
    if noun == category:
        return True
    if noun in scene_categories:
        return False
    return category in noun_class(noun)


def match_objects(words: NameWords, objects: Sequence[Nameable]) -> list[str]:
    """Ids of the objects consistent with every word. A colour or size word does not rule out an
    object that has no colour or size word of its own (nothing to contradict)."""
    if not (words.colours or words.sizes or words.nouns):
        return []
    categories = {o.category for o in objects}
    out = []
    for o in objects:
        if any(not noun_matches(n, o.category, categories) for n in words.nouns):
            continue
        if words.colours and o.color_name is not None and o.color_name not in words.colours:
            continue
        if words.sizes and o.size_word is not None and o.size_word not in words.sizes:
            continue
        out.append(o.id)
    return sorted(out)


@dataclass(frozen=True)
class Resolution:
    """What a free-text object answer names in one scene.

    kind: object (exactly one match, `ids` has it), table, none, ambiguous (several matches),
    no_match (readable words, no object), unparseable (no naming words, or conflicting ones).
    """

    kind: str
    ids: tuple[str, ...] = ()
    words: NameWords | None = None


def resolve_object(text: object, objects: Sequence[Nameable]) -> Resolution:
    if not isinstance(text, str):
        return Resolution("unparseable")
    words = parse_name_words(text, extra_nouns={o.category for o in objects})
    if words.empty or not words.single:
        return Resolution("unparseable", words=words)
    has_object_words = bool(words.colours or words.sizes or words.nouns)
    if words.table and not has_object_words:
        return Resolution("table", words=words)
    if words.none and not has_object_words:
        return Resolution("none", words=words)
    if not has_object_words:
        return Resolution("unparseable", words=words)
    ids = match_objects(words, objects)
    if len(ids) == 1:
        return Resolution("object", tuple(ids), words)
    return Resolution("ambiguous" if ids else "no_match", tuple(ids), words)


_NONE_FILLERS = {"the", "there", "are", "is", "objects", "object", "no", "of", "them", "at", "all"}


def split_items(value: object) -> list[object] | None:
    """A list, or a comma/'and'-separated string, -> items (an empty list for 'none')."""
    if isinstance(value, str):
        toks = set(_tokens(value))
        if not toks:
            return None
        if toks & (NONE_WORDS | {"no"}) and toks <= NONE_WORDS | _NONE_FILLERS:
            return []
        return [p for p in re.split(r",|;|\band\b|\n", value) if p.strip()]
    if isinstance(value, list | tuple):
        return list(value)
    return None


def name_candidates(obj: Nameable) -> list[str]:
    """Naming-rule forms in order: category, colour category, size category, size colour
    category (forms the object has no words for are skipped)."""
    forms = [[obj.category]]
    if obj.color_name:
        forms.append([obj.color_name, obj.category])
    if obj.size_word:
        forms.append([obj.size_word, obj.category])
        if obj.color_name:
            forms.append([obj.size_word, obj.color_name, obj.category])
    return [" ".join(f) for f in forms]


def unique_name(obj: Nameable, objects: Sequence[Nameable]) -> str | None:
    """The first naming-rule form that resolves to this object alone, or None (unnameable)."""
    for name in name_candidates(obj):
        r = resolve_object(name, objects)
        if r.kind == "object" and r.ids == (obj.id,):
            return name
    return None


def full_name(obj: Nameable) -> str:
    """[size] [colour] category, as ObjectRef.name and SceneObject.name."""
    return " ".join(w for w in (obj.size_word, obj.color_name, obj.category) if w)


# ---------------------------------------------------------------------------------------------
# v0 normalisers (v0.1 tasks, no scene objects) and typed normalisers


def normalise_object(value: object) -> str | None:
    """v0: 'The red cube.' -> 'red box'. None unless exactly one colour and one shape are named."""
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


_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")
_UNIT_TO_CM = (
    (re.compile(r"\b(mm|millimet(?:re|er)s?)\b"), 0.1),
    (re.compile(r"\b(cm|centimet(?:re|er)s?)\b"), 1.0),
    (re.compile(r"\b(m|met(?:re|er)s?)\b"), 100.0),
    (re.compile(r"\b(inch|inches)\b"), 2.54),
)


def normalise_number(value: object) -> float | None:
    """A length in centimetres: 12, 12.5, '12.5 cm', 'about 12 centimetres', '0.12 m' -> 12.0.

    Bare numbers are centimetres (the unit every distance question asks for); an explicit unit
    converts. None unless exactly one number is given.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.lower()
    numbers = {float(n.replace(",", ".")) for n in _NUMBER.findall(text)}
    if not numbers:
        words = [NUMBER_WORDS[t] for t in _tokens(text) if t in NUMBER_WORDS]
        numbers = {float(w) for w in words}
    if len(numbers) != 1:
        return None
    number = numbers.pop()
    for pattern, factor in _UNIT_TO_CM:
        if pattern.search(text):
            return number * factor
    return number


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
    """v0: a list, or a comma/'and'-separated string, of object names -> sorted canonical names."""
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
    """Dispatch on a TaskPackage answer_type: int, object, set, bool or number (v0 names)."""
    return {
        "int": normalise_int,
        "object": normalise_object,
        "set": normalise_set,
        "bool": normalise_bool,
        "number": normalise_number,
    }[answer_type](value)
