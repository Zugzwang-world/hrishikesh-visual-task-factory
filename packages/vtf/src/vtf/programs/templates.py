"""Question phrasings: two per program. `compile_questions` picks one with the seeded rng and
stores the other in CompiledQuestion.question_alt.

Template ids are `<program>.p1` and `<program>.p2`. Object slots take unique names (naming rule,
spec/families.md) and are written with "the" in front. Every camera-frame relation names its
frame: {Frame} is "From the camera's view" on the front and overhead rigs and "In the gripper
camera's view" on the gripper rig; {camera} is "the camera" or "the gripper camera". Table-plane
relations (distance, free space, zones) say "on the table".
"""

TEMPLATES: dict[str, tuple[str, str]] = {
    "count_all": (
        "How many objects are in the scene? Do not count the table or the gripper.",
        "How many objects are on the table, including stacked objects and objects inside "
        "containers?",
    ),
    "count_category": (
        "How many {plural} are in the scene?",
        "How many {plural} are on the table, including stacked ones and ones inside containers?",
    ),
    "count_color": (
        "How many {color} objects are in the scene?",
        "How many {color} objects are on the table, including stacked objects and objects "
        "inside containers?",
    ),
    "count_color_in": (
        "How many {color} objects are inside the {container}?",
        "Count the {color} objects that are inside the {container}. How many are there?",
    ),
    "nearest_to_gripper": (
        "Which object is closest to the tip of the gripper?",
        "Which object is nearest to the gripper's tip?",
    ),
    "distance_cm": (
        "On the table, what is the horizontal distance between the centre of the {a} and the "
        "centre of the {b}, in centimetres?",
        "In centimetres, how far apart are the centres of the {a} and the {b}, measured along "
        "the table?",
    ),
    "front_to_back": (
        "Which is closer to {camera}, the {a} or the {b}?",
        "Of the {a} and the {b}, which one is nearer to {camera}?",
    ),
    "relation": (
        "{Frame}, which object is the nearest one directly to the {direction} of the {anchor}?",
        "{Frame}, looking directly to the {direction} of the {anchor}, which object is closest "
        "to it?",
    ),
    "support": (
        "What is the {a} resting on?",
        "What is directly supporting the {a}: another object, or the table?",
    ),
    "contents": (
        "Which objects are inside the {container}?",
        "List the objects that are inside the {container}.",
    ),
    "occluded_by": (
        "{Frame}, which object is partly hidden by the {occluder}? If several are, name the one "
        "it hides most.",
        "{Frame}, which object does the {occluder} block from view the most?",
    ),
    "in_zone": (
        "Which objects have their centre inside the {zone} marked on the table?",
        "Which objects are centred within the {zone} on the table?",
    ),
    "room_between": (
        "Is there room to place the {item} on the table between the {a} and the {b}?",
        "Would the {item} fit on the table in the gap between the {a} and the {b}?",
    ),
    "gripper_center": (
        "In the gripper camera's view, which object is at the centre of the image?",
        "In the gripper camera's view, which object lies directly at the image centre?",
    ),
}


def frame_slots(rig: str) -> dict[str, str]:
    if rig == "gripper":
        return {"Frame": "In the gripper camera's view", "camera": "the gripper camera"}
    return {"Frame": "From the camera's view", "camera": "the camera"}


def phrase(program: str, index: int, rig: str = "front", **slots: str) -> tuple[str, str, str]:
    """(template_id, question text, the other phrasing) for one program and phrasing index."""
    values = {**frame_slots(rig), **slots}
    texts = [t.format(**values) for t in TEMPLATES[program]]
    return f"{program}.p{index + 1}", texts[index], texts[1 - index]
