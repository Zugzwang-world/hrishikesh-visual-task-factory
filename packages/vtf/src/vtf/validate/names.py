"""Object naming per spec/families.md, and resolving an answer's names back to object ids.

Naming rule: an object's name is the first of `category`, `colour category`, `size category`,
`size colour category` that is unique among all scene objects; an object with none is never
named. Resolving goes the other way: a name picks the one object whose words match it, so G2 can
compare object ids whatever naming helper the programs used, as long as it follows the rule.
"""

from vtf.core.scene import SceneObject, SceneSpec

PATTERNS = (("category",), ("color_name", "category"), ("size_word", "category"),
            ("size_word", "color_name", "category"))  # fmt: skip
TABLE = "table"


def _words(obj: SceneObject, pattern: tuple[str, ...]) -> tuple[str, ...] | None:
    words = tuple(getattr(obj, f) for f in pattern)
    return None if any(w is None for w in words) else words


def unique_name(obj: SceneObject, scene: SceneSpec) -> str | None:
    """The object's name under the naming rule, or None when no pattern is unique."""
    for pattern in PATTERNS:
        words = _words(obj, pattern)
        if words is None:
            continue
        if sum(1 for o in scene.objects if _words(o, pattern) == words) == 1:
            return " ".join(words)
    return None


def display_name(obj: SceneObject, scene: SceneSpec) -> str:
    """Unique name when there is one, else the full descriptive name."""
    return unique_name(obj, scene) or obj.name


def _variants(obj: SceneObject) -> set[str]:
    out = {obj.name}
    for pattern in PATTERNS:
        words = _words(obj, pattern)
        if words is not None:
            out.add(" ".join(words))
    return out


def _norm(text: str) -> str:
    return " ".join(str(text).lower().replace("_", " ").split())


def resolve(scene: SceneSpec, name: str) -> str | None:
    """A name (or an object id) -> the one object id it denotes, 'table', or None."""
    if not isinstance(name, str):
        return None
    if _norm(name) == TABLE:
        return TABLE
    ids = {o.id for o in scene.objects}
    if name in ids:
        return name
    hits = [o.id for o in scene.objects if _norm(name) in {_norm(v) for v in _variants(o)}]
    return hits[0] if len(hits) == 1 else None


def resolve_param(scene: SceneSpec, value: object) -> str | None:
    """A program parameter naming an object: its id, or a name that resolves to one."""
    return resolve(scene, value) if isinstance(value, str) else None


def name_of(scene: SceneSpec, object_id: str) -> str:
    if object_id == TABLE:
        return TABLE
    return display_name(scene.object(object_id), scene)
