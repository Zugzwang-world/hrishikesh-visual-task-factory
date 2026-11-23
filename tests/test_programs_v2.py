"""Question programs v0.2 on hand-built settled asset scenes (vtf.programs.handbuilt): every
family accepts a question with trace and margins, compile_questions is seeded and honours the
question mix and rigs, support and containment follow the resting and inside rules, translating
the scene changes no answer, and every twin edit changes the answer. Plus the v0.2 scorer.
"""

import json
import subprocess
import sys
from functools import cache

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from vtf.core.geometry import Pose
from vtf.core.io import write_model
from vtf.core.program import FAMILIES, CompiledQuestion, ProgramReject
from vtf.core.scene import QuestionMix, SceneSpec
from vtf.core.task import ObjectRef
from vtf.core.vocab import (
    default_plural,
    parse_name_words,
    plural,
    resolve_object,
    unique_name,
)
from vtf.eval.prompt import ANSWER_TYPES, response_schema, system_prompt
from vtf.eval.scorer import score_response
from vtf.programs import compile_questions, make_twin, object_refs, run_program
from vtf.programs.handbuilt import SCENES, rest_copy
from vtf.programs.twins import SPAWN_LIFT_M
from vtf.programs.view import camera_view

SEED = 7


@cache
def scene(name: str) -> SceneSpec:
    return SCENES[name]()


@cache
def compiled(name: str, camera_id: str) -> tuple[CompiledQuestion, ...]:
    return tuple(compile_questions(scene(name), camera_id, SEED, max_questions=6))


def accepted() -> list[tuple[str, str, CompiledQuestion]]:
    return [(n, c.id, q) for n in SCENES for c in scene(n).cameras
            for q in compiled(n, c.id) if q.rejected is None]  # fmt: skip


def translated(s: SceneSpec, dx: float, dy: float) -> SceneSpec:
    def move(pose: Pose) -> Pose:
        x, y, z = pose.position
        return Pose(position=(x + dx, y + dy, z), rotation=pose.rotation)

    g = s.gripper
    return s.model_copy(update={
        "objects": [o.model_copy(update={"pose": move(o.pose)}) for o in s.objects],
        "cameras": [c.model_copy(update={"pose": move(c.pose)}) for c in s.cameras],
        "gripper": g.model_copy(update={"tip": move(g.tip), "parts": [
            p.model_copy(update={"pose": move(p.pose)}) for p in g.parts]}),
        "zones": [z.model_copy(update={"center_xy": (z.center_xy[0] + dx, z.center_xy[1] + dy)})
                  for z in s.zones],
    })  # fmt: skip


# ---------------------------------------------------------------------------------------------
# every family, on the hand-built scenes


def test_every_family_accepts_with_trace_and_margins() -> None:
    by_family: dict[str, list] = {}
    for _, _, q in accepted():
        by_family.setdefault(q.family, []).append(q)
    assert set(by_family) == set(FAMILIES)
    for family, qs in by_family.items():
        spec = FAMILIES[family]
        for q in qs:
            r = q.result
            assert r.trace and r.answer_type == spec.answer_type
            assert (r.shortcut_conflict is not None) == spec.spatial, family
            assert "n_objects" in r.difficulty and "evidence_min_visible" in r.difficulty
            if family in ("nearest_to_gripper", "front_to_back", "free_space"):
                assert r.margin is not None and r.margin_px is not None
            if family == "distance":
                assert r.tolerance == pytest.approx(max(2.0, 0.1 * r.answer), abs=0.01)
            if family == "gripper_view":
                assert r.margin_px >= 15


def test_compile_is_seeded_and_respects_rigs_and_limits() -> None:
    for name in SCENES:
        s = scene(name)
        for cam in s.cameras:
            qs = compiled(name, cam.id)
            again = compile_questions(s, cam.id, SEED, max_questions=6)
            assert [q.model_dump() for q in again] == [q.model_dump() for q in qs]
            ok = [q for q in qs if q.rejected is None]
            assert 3 <= len(ok) <= min(6, s.questions.max_per_camera)
            for q in qs:
                assert cam.rig in FAMILIES[q.family].rigs
                assert q.template_id in {f"{q.program.name}.p1", f"{q.program.name}.p2"}
                assert q.question_alt and q.question_alt != q.question
                assert (q.result is None) == (q.rejected is not None)
                if cam.rig == "gripper" and q.family in ("relation", "occlusion"):
                    assert "gripper camera's view" in q.question
                CompiledQuestion.model_validate_json(q.model_dump_json())
            for q in ok:  # what G1 does
                assert q.result == run_program(q.program.name, q.program.params, s, cam.id)
                view = camera_view(s, cam.id)
                for oid in q.result.evidence_object_ids:
                    assert view.fraction(oid) >= 0.15


def test_compile_honours_question_mix() -> None:
    s = scene("kitchen")
    only = s.model_copy(update={"questions": QuestionMix(families=["count", "support"],
                                                         max_per_camera=2)})  # fmt: skip
    qs = compile_questions(only, "front", 3)
    assert {q.family for q in qs} <= {"count", "support"}
    assert sum(q.rejected is None for q in qs) <= 2
    weighted = s.model_copy(update={"questions": QuestionMix(weights={"distance": 0.0})})
    assert all(q.family != "distance" for q in compile_questions(weighted, "front", 3))
    assert all(q.family != "gripper_view" for q in compile_questions(s, "front", 3))


# ---------------------------------------------------------------------------------------------
# support, containment and naming on asset scenes


def test_support_and_containment_follow_the_placement() -> None:
    for name in SCENES:
        s = scene(name)
        for o in s.objects:
            try:
                r = run_program("support", {"a": o.id}, s, "front")
            except ProgramReject as rej:
                assert rej.reason == "unnameable"
                continue
            parent = o.placement.parent_id
            expected = "table" if parent is None else resolve_object(r.answer, s.objects).ids[0]
            assert (r.answer == "table") == (parent is None)
            assert expected in ("table", parent)
        for c in s.objects:
            if c.placement.kind == "table" and any(o.placement.kind == "in" and
                                                   o.placement.parent_id == c.id
                                                   for o in s.objects):
                r = run_program("contents", {"container": c.id}, s, "overhead")
                inside = sorted(o.id for o in s.objects
                                if o.placement.kind == "in" and o.placement.parent_id == c.id)
                got = sorted(resolve_object(n, s.objects).ids[0] for n in r.answer)
                assert got == inside


def test_unique_names_resolve_back_and_duplicates_are_unnamed() -> None:
    for name in SCENES:
        objs = scene(name).objects
        for o in objs:
            n = unique_name(o, objs)
            if n is not None:
                assert resolve_object(n, objs).ids == (o.id,)
    pantry = scene("pantry").objects
    lemons = [o for o in pantry if o.category == "lemon"]
    assert len(lemons) == 2 and all(unique_name(o, pantry) is None for o in lemons)
    rocks = {unique_name(o, pantry) for o in pantry if o.category == "rock"}
    assert rocks == {"large rock", "small rock"}
    refs = object_refs(scene("pantry"))
    assert [r.id for r in refs] == sorted(o.id for o in pantry)


def test_vocab_nouns_plurals_and_colour_words() -> None:
    assert plural("pliers") == "pliers" and plural("apple") == "apples"
    assert default_plural("box") == "boxes" and default_plural("lychee") == "lychees"
    w = parse_name_words("the large red apples")
    assert w.sizes == ("large",) and w.colours == ("red",) and w.nouns == ("apple",)
    assert parse_name_words("the orange", extra_nouns={"orange"}).nouns == ("orange",)
    w = parse_name_words("orange orange", extra_nouns={"orange"})
    assert w.colours == ("orange",) and w.nouns == ("orange",)
    assert parse_name_words("a gray cube").colours == ("grey",)


# ---------------------------------------------------------------------------------------------
# invariance: translating the whole scene and its cameras in xy changes no answer


@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(name=st.sampled_from(sorted(SCENES)),
       shift=st.tuples(st.floats(-0.02, 0.02), st.floats(-0.02, 0.02)))  # fmt: skip
def test_translation_changes_no_answer(name, shift) -> None:
    s = scene(name)
    moved = translated(s, *shift)
    for cam in s.cameras:
        for q in compiled(name, cam.id):
            if q.rejected:
                continue
            try:
                r = run_program(q.program.name, q.program.params, moved, cam.id)
            except ProgramReject:
                continue  # a shifted pixel may tip a pixel near tie; answers must still agree
            assert r.answer == q.result.answer, (name, cam.id, q.question)


# ---------------------------------------------------------------------------------------------
# twins


def apply_twin(s: SceneSpec, twin) -> SceneSpec:
    """Build the twin scene from a TwinEdit the way validate does, then let it 'settle' by
    taking the spawn lift back off."""
    if twin.remove:
        objects = [o for o in s.objects if o.id != twin.object_id]
    else:
        x, y, z = twin.new_pose.position
        pose = Pose(position=(x, y, z - SPAWN_LIFT_M), rotation=twin.new_pose.rotation)
        objects = [o.model_copy(update={"pose": pose}) if o.id == twin.object_id else o
                   for o in s.objects]  # fmt: skip
    return s.model_copy(update={"objects": objects})


def test_every_twin_edit_changes_the_answer() -> None:
    made: dict[str, int] = {}
    for name, cam, q in accepted():
        s = scene(name)
        twin = make_twin(q.program.name, q.program.params, s, cam, seed=3)
        if twin is None:
            continue
        made[q.family] = made.get(q.family, 0) + 1
        assert twin.remove or twin.new_pose is not None
        if not twin.remove:
            moved = s.object(twin.object_id)
            assert twin.new_pose.rotation == moved.pose.rotation
        result = run_program(q.program.name, q.program.params, apply_twin(s, twin), cam)
        assert result.answer == twin.expected_answer != q.result.answer, (name, q.question)
        assert make_twin(q.program.name, q.program.params, s, cam, seed=3) == twin
    for family in ("count", "count_attribute", "containment", "reachability", "support",
                   "distance", "relation", "free_space"):
        assert made.get(family), family


def test_rest_copy_settles_sampled_placements() -> None:
    s = scene("kitchen")
    lifted = s.model_copy(update={
        "meta": s.meta.model_copy(update={"settled": False}),
        "objects": [o.model_copy(update={"pose": Pose(
            position=(o.pose.position[0], o.pose.position[1], o.pose.position[2] + 0.01),
            rotation=o.pose.rotation)}) for o in s.objects],
    })  # fmt: skip
    again = rest_copy(lifted)
    assert again.meta.settled
    for a, b in zip(again.objects, s.objects, strict=True):
        assert a.pose.position[2] == pytest.approx(b.pose.position[2], abs=2e-3)


def test_cli_prints_questions(tmp_path) -> None:
    path = write_model(tmp_path / "scene.settled.json", scene("tabletop"))
    out = subprocess.run([sys.executable, "-m", "vtf.programs", str(path), "--camera",
                          "overhead", "--twins"], capture_output=True, text=True, check=True)
    assert "accepted" in out.stdout and "->" in out.stdout and "twin:" in out.stdout


# ---------------------------------------------------------------------------------------------
# scorer v0.2: names resolved against the task's scene objects


REFS = [
    ObjectRef(id="obj_00", name="red mug", category="mug", color_name="red"),
    ObjectRef(id="obj_01", name="large grey rock", category="rock", color_name="grey",
              size_word="large"),
    ObjectRef(id="obj_02", name="small grey rock", category="rock", color_name="grey",
              size_word="small"),
    ObjectRef(id="obj_03", name="yellow lemon", category="lemon", color_name="yellow"),
    ObjectRef(id="obj_04", name="yellow lemon", category="lemon", color_name="yellow"),
    ObjectRef(id="obj_05", name="cube", category="cube", color_name=None),
]


def score(answer_type, truth, value, tol=None):
    return score_response(answer_type, truth, json.dumps({"answer": value}), tol, REFS)


@pytest.mark.parametrize(
    "truth, value, parse_ok, correct",
    [
        ("mug", "the red mug", True, True),  # extra colour word, still exactly one object
        ("mug", "Mug.", True, True),
        ("mug", "blue mug", True, False),  # contradicts the mug's colour: matches nothing
        ("large rock", "the big rock", True, True),
        ("large rock", "rock", True, False),  # two rocks: ambiguous
        ("large rock", "small grey rock", True, False),
        ("cube", "the green cube", True, True),  # the cube has no colour word to contradict
        ("cube", "box", True, True),  # synonym, no box category in the scene
        ("table", "the table", True, True),
        ("mug", "the table", True, False),
        ("mug", "something shiny", False, False),
        ("mug", "red or yellow mug", False, False),
    ],
)
def test_object_answers_resolve_against_scene_objects(truth, value, parse_ok, correct) -> None:
    s = score("object", truth, value)
    assert (s.parse_ok, s.correct) == (parse_ok, correct)


def test_set_number_and_legacy_answers() -> None:
    assert score("set", ["large rock", "mug"], ["red mug", "the large rock"]).correct
    assert not score("set", ["large rock", "mug"], ["mug"]).correct
    assert not score("set", ["mug"], ["mug", "lemon"]).correct  # lemon is ambiguous
    assert score("set", [], "none").correct and score("set", [], []).correct
    assert not score("set", ["mug"], "none").correct
    assert not score("set", ["mug"], "several shiny things").parse_ok
    assert score("number", 23.4, 25, 2.34).correct
    assert score("number", 23.4, "0.25 m", 2.34).correct
    assert not score("number", 23.4, "30 cm", 2.34).correct
    assert not score("number", 23.4, "between 20 and 30", 2.34).parse_ok
    legacy = score_response("object", "red box", '{"answer": "the red cube"}')
    assert legacy.correct and legacy.parsed == "red box"


def test_prompts_cover_every_answer_type_without_examples() -> None:
    assert set(ANSWER_TYPES) == {"int", "object", "set", "bool", "number"}
    assert "centimetres" in system_prompt("number")
    assert response_schema("number")["properties"]["answer"] == {"type": "number"}
    for t in ("object", "set"):
        text = system_prompt(t)
        assert "colour" in text and "small or large" in text
        assert "mug" not in text and "e.g." not in text
