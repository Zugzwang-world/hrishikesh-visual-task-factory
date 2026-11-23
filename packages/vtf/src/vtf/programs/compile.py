"""compile_questions: seeded choice and phrasing of candidate questions for one scene and camera."""

from collections import Counter
from itertools import combinations

from vtf.core.program import CompiledQuestion, ProgramRef, ProgramReject
from vtf.core.rng import make_rng
from vtf.core.scene import SceneSpec
from vtf.programs.library import PROGRAMS, canonical_name, run_program
from vtf.programs.templates import TEMPLATES, phrase

FRONT_TO_BACK_TRIES = 3
FRONT_TO_BACK_ACCEPT = 2


def compile_questions(
    scene: SceneSpec, camera_id: str, seed: int, max_questions: int = 5
) -> list[CompiledQuestion]:
    """Propose, phrase and run candidate questions; same inputs give identical output.

    Proposals, in order: one count_all; one count_color for a colour present in the scene
    (colours with two or more objects preferred); one nearest_to_gripper; up to three
    front_to_back pairs, stopping after two are accepted. Proposing stops once `max_questions`
    are accepted. Returns the accepted questions and every rejected candidate, in proposal order.
    """
    if not scene.meta.settled:
        raise ValueError(f"{scene.meta.scene_id}: programs run on a settled scene only")
    scene.camera(camera_id)
    rng = make_rng(seed)
    objects = sorted(scene.objects, key=lambda o: o.id)
    out: list[CompiledQuestion] = []
    n_accepted = 0

    def propose(program: str, params: dict, **slots: str) -> bool:
        nonlocal n_accepted
        index = int(rng.integers(len(TEMPLATES[program])))
        template_id, text = phrase(program, index, **slots)
        base = {
            "family": PROGRAMS[program].family,
            "template_id": template_id,
            "question": text,
            "camera_id": camera_id,
            "program": ProgramRef(name=program, params=params),
        }
        try:
            result = run_program(program, params, scene, camera_id)
        except ProgramReject as rej:
            out.append(CompiledQuestion(**base, rejected=rej.reason, rejected_detail=rej.detail))
            return False
        out.append(CompiledQuestion(**base, result=result))
        n_accepted += 1
        return True

    def full() -> bool:
        return n_accepted >= max_questions

    if not full():
        propose("count_all", {})

    colour_counts = Counter(o.color_name for o in objects if o.color_name)
    colours = sorted(c for c, n in colour_counts.items() if n >= 2) or sorted(colour_counts)
    if colours and not full():
        colour = colours[int(rng.integers(len(colours)))]
        propose("count_color", {"color": colour}, color=colour)

    if not full():
        propose("nearest_to_gripper", {})

    pairs = list(combinations(objects, 2))
    tried = ok = 0
    for i in rng.permutation(len(pairs)) if pairs else []:
        if tried >= FRONT_TO_BACK_TRIES or ok >= FRONT_TO_BACK_ACCEPT or full():
            break
        a, b = pairs[int(i)]
        if rng.integers(2):
            a, b = b, a
        tried += 1
        ok += propose(
            "front_to_back", {"a": a.id, "b": b.id}, a=canonical_name(a), b=canonical_name(b)
        )
    return out
