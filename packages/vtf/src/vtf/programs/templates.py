"""Question phrasings: two per program, chosen by the seeded rng in `compile_questions`.

Template ids are `<program>.p1` and `<program>.p2`. Object slots take canonical names
("red box") and are written with "the" in front.
"""

TEMPLATES: dict[str, tuple[str, ...]] = {
    "count_all": (
        "How many objects are on the table?",
        "Count the objects on the table. How many are there?",
    ),
    "count_color": (
        "How many {color} objects are on the table?",
        "Count the {color} objects on the table. How many are there?",
    ),
    "nearest_to_gripper": (
        "Which object is closest to the tip of the gripper?",
        "Which object is nearest to the gripper's tip?",
    ),
    "front_to_back": (
        "Which is closer to the camera, the {a} or the {b}?",
        "Of the {a} and the {b}, which one is nearer to the camera?",
    ),
}


def phrase(program: str, index: int, **slots: str) -> tuple[str, str]:
    """(template_id, question text) for one program, phrasing index and slot values."""
    text = TEMPLATES[program][index].format(**slots)
    return f"{program}.p{index + 1}", text
