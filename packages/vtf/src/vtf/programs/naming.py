"""The naming rule on a scene (spec/families.md), for programs, packaging and the scorer."""

from vtf.core.scene import SceneSpec
from vtf.core.task import ObjectRef
from vtf.core.vocab import full_name, unique_name


def unique_names(scene: SceneSpec) -> dict[str, str | None]:
    """object id -> its unique name, or None when the object cannot be named alone."""
    objects = sorted(scene.objects, key=lambda o: o.id)
    return {o.id: unique_name(o, objects) for o in objects}


def object_refs(scene: SceneSpec) -> list[ObjectRef]:
    """TaskPackage.scene_objects: every object with the words that can name it."""
    return [
        ObjectRef(id=o.id, name=full_name(o), category=o.category, color_name=o.color_name,
                  size_word=o.size_word)
        for o in sorted(scene.objects, key=lambda o: o.id)
    ]  # fmt: skip
