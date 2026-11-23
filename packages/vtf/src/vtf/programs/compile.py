"""compile_questions: seeded choice and phrasing of candidate questions for one scene and camera.

Families are visited in a seeded, weighted order (scene.questions.weights; families outside
scene.questions.families or outside the family's camera rigs are skipped). Each visit tries up to
TRIES_PER_VISIT candidates of that family until one is accepted; a second round runs only if the
first leaves room, so a scene yields a spread of families rather than many of one. Candidates are
ordered so that the likely ones come first (named objects nameable and visible in this camera);
counts whose counted objects are not all visible (rule A) are not proposed.

After a program accepts, the evidence objects must be visible in this camera by the G3
thresholds of spec/families.md (exact ray cast of the scene file); otherwise the candidate is
returned as rejected (`evidence_hidden`). distance and free_space are proposed only when the
scale-in-frame rule holds for the camera.
"""

from itertools import combinations
from typing import Any

import numpy as np

from vtf.core.program import FAMILIES, CompiledQuestion, ProgramRef, ProgramReject
from vtf.core.rng import make_rng
from vtf.core.scene import SceneSpec
from vtf.core.vocab import plural
from vtf.programs.geometry import diameter, min_width, polygon_distance, scene_geom
from vtf.programs.library import (
    NEAR_TIE_DEPTH_FRONT_M,
    NEAR_TIE_DEPTH_M,
    PROGRAMS,
    run_program,
    zone_label,
)
from vtf.programs.templates import phrase
from vtf.programs.view import camera_view, hidden_evidence, planar_depth, scale_reference

TRIES_PER_VISIT = 4
ROUNDS = 2
MAX_PAIR_CANDIDATES = 24
FREE_SPACE_MAX_GAP_M = 0.30
ROUND_ITEM_MIN = 0.7  # free_space items: min width / largest extent

Candidate = tuple[str, dict[str, Any], dict[str, str]]  # program, params, template slots


def _shuffled(rng: np.random.Generator, items: list) -> list:
    return [items[int(i)] for i in rng.permutation(len(items))] if items else []


class _Proposer:
    """Builds each family's candidate queue for one scene and camera."""

    def __init__(self, scene: SceneSpec, camera_id: str, rng: np.random.Generator) -> None:
        self.scene = scene
        self.cam = scene.camera(camera_id)
        self.rng = rng
        self.sg = scene_geom(scene)
        self.view = camera_view(scene, camera_id)
        self.ids = [o.id for o in self.sg.objects]
        self.named = [i for i in self.ids if self.sg.name(i) and self.view.visible(i)]
        self.scale = scale_reference(scene, self.view)

    def name(self, oid: str) -> str:
        return self.sg.name(oid) or oid

    def count(self) -> list[Candidate]:
        cats = sorted({o.category for o in self.sg.objects})
        cands: list[Candidate] = [("count_all", {}, {})]
        cands += [("count_category", {"category": c}, {"plural": plural(c)}) for c in cats]
        return self._by_counted_visibility(cands)

    def count_attribute(self) -> list[Candidate]:
        colours = sorted({o.color_name for o in self.sg.objects if o.color_name})
        cands: list[Candidate] = [("count_color", {"color": c}, {"color": c}) for c in colours]
        for cid in self.sg.containers():
            if cid not in self.named:
                continue
            inside = {self.scene.object(i).color_name for i in self.sg.contents(cid)}
            ordered = sorted(c for c in colours if c in inside) + _shuffled(
                self.rng, sorted(c for c in colours if c not in inside))[:2]  # fmt: skip
            cands += [("count_color_in", {"color": c, "container": cid},
                       {"color": c, "container": self.name(cid)}) for c in ordered]  # fmt: skip
        return self._by_counted_visibility(cands)

    def _by_counted_visibility(self, cands: list[Candidate]) -> list[Candidate]:
        cands = _shuffled(self.rng, cands)

        def ok(c: Candidate) -> bool:
            prog, params, _ = c
            objs = self.sg.objects
            if prog == "count_category":
                ids = [o.id for o in objs if o.category == params["category"]]
            elif prog == "count_color":
                ids = [o.id for o in objs if o.color_name == params["color"]]
            elif prog == "count_color_in":
                ids = [i for i in self.sg.contents(params["container"])
                       if self.scene.object(i).color_name == params["color"]]  # fmt: skip
            else:
                ids = [o.id for o in objs]
            return all(self.view.visible(i, counted=True) for i in ids)

        # counted objects are known before running: never propose a count whose objects are
        # not all visible in this camera (rule A), it could only fail G3
        return [c for c in cands if ok(c)]

    def nearest_to_gripper(self) -> list[Candidate]:
        return [("nearest_to_gripper", {}, {})]

    def distance(self) -> list[Candidate]:
        if self.scale is None:
            return []
        pairs = _shuffled(self.rng, list(combinations(self.named, 2)))[:MAX_PAIR_CANDIDATES]
        return [("distance_cm", {"a": a, "b": b}, {"a": self.name(a), "b": self.name(b)})
                for a, b in (self._swap(p) for p in pairs)]  # fmt: skip

    def _swap(self, pair: tuple[str, str]) -> tuple[str, str]:
        return (pair[1], pair[0]) if self.rng.integers(2) else pair

    def front_to_back(self) -> list[Candidate]:
        threshold = NEAR_TIE_DEPTH_FRONT_M if self.cam.rig == "front" else NEAR_TIE_DEPTH_M
        depth = {i: planar_depth(self.cam, self.sg.geom[i].centre) for i in self.named}
        pairs = [self._swap(p) for p in _shuffled(self.rng, list(combinations(self.named, 2)))]
        good = [p for p in pairs if abs(depth[p[0]] - depth[p[1]]) >= threshold]
        rest = [p for p in pairs if abs(depth[p[0]] - depth[p[1]]) < threshold]
        return [("front_to_back", {"a": a, "b": b}, {"a": self.name(a), "b": self.name(b)})
                for a, b in (good + rest)[:MAX_PAIR_CANDIDATES]]  # fmt: skip

    def relation(self) -> list[Candidate]:
        combos = _shuffled(self.rng, [(a, d) for a in self.named for d in ("left", "right")])
        return [("relation", {"anchor": a, "direction": d}, {"anchor": self.name(a),
                                                             "direction": d})
                for a, d in combos]  # fmt: skip

    def support(self) -> list[Candidate]:
        raised = _shuffled(self.rng, [i for i in self.named
                                      if self.sg.placement_kind(i) in ("stacked", "contained")])
        flat = _shuffled(self.rng, [i for i in self.named if self.sg.placement_kind(i) == "table"])
        order = raised + flat if self.rng.random() < 0.75 else flat[:1] + raised + flat[1:]
        return [("support", {"a": i}, {"a": self.name(i)}) for i in order]

    def containment(self) -> list[Candidate]:
        cids = _shuffled(self.rng, [c for c in self.sg.containers() if c in self.named])
        return [("contents", {"container": c}, {"container": self.name(c)}) for c in cids]

    def occlusion(self) -> list[Candidate]:
        index = self.view.index
        scored = []
        for oid in self.named:
            mask = index == self.scene.object(oid).pass_index
            best = 0.0
            for other in self.sg.objects:
                am = self.view.amodal.get(other.pass_index)
                if other.id == oid or am is None or not am.any():
                    continue
                best = max(best, float((am & mask).sum()) / float(am.sum()))
            scored.append((oid, best))
        scored = _shuffled(self.rng, scored)
        good = [o for o, s in scored if s >= 0.10]
        rest = [o for o, s in scored if s < 0.10]
        return [("occluded_by", {"occluder": o}, {"occluder": self.name(o)})
                for o in good + rest[:2]]  # fmt: skip

    def reachability(self) -> list[Candidate]:
        out = []
        for z in _shuffled(self.rng, list(self.scene.zones)):
            label = zone_label(self.scene, z)
            if label:
                out.append(("in_zone", {"zone": z.id}, {"zone": label}))
        return out

    def free_space(self) -> list[Candidate]:
        if self.scale is None:
            return []
        on_table = [i for i in self.named if self.sg.support(i).support_id == "table"]
        items = []
        for i in self.named:
            hull = self.sg.geom[i].hull
            if min_width(hull) >= ROUND_ITEM_MIN * diameter(hull):
                items.append(i)
        yes, no = [], []
        for a, b in combinations(on_table, 2):
            gap = polygon_distance(self.sg.geom[a].hull, self.sg.geom[b].hull)
            if gap > FREE_SPACE_MAX_GAP_M:
                continue
            for item in items:
                if item in (a, b):
                    continue
                w = diameter(self.sg.geom[item].hull)
                (yes if gap >= w + 0.02 else no).append((item, a, b))
        yes, no = _shuffled(self.rng, yes), _shuffled(self.rng, no)
        mixed = [t for pair in zip(yes, no, strict=False) for t in pair]
        mixed += yes[len(no):] + no[len(yes):]
        if self.rng.integers(2) and len(mixed) > 1:
            mixed[0], mixed[1] = mixed[1], mixed[0]
        return [("room_between", {"item": i, "a": a, "b": b},
                 {"item": self.name(i), "a": self.name(a), "b": self.name(b)})
                for i, a, b in mixed[:MAX_PAIR_CANDIDATES]]  # fmt: skip

    def gripper_view(self) -> list[Candidate]:
        return [("gripper_center", {}, {})]

    def queue(self, family: str) -> list[Candidate]:
        return getattr(self, family)()


def family_order(families: list[str], weights: dict[str, float],
                 rng: np.random.Generator) -> list[str]:  # fmt: skip
    """Seeded weighted order without replacement (Efraimidis-Spirakis keys u ** (1 / w))."""
    keyed = []
    for f in families:
        w = float(weights.get(f, 1.0))
        u = float(rng.random())
        if w > 0:
            keyed.append((-(u ** (1.0 / w)) if u > 0 else 0.0, f))
    return [f for _, f in sorted(keyed)]


def compile_questions(
    scene: SceneSpec, camera_id: str, seed: int, max_questions: int = 5
) -> list[CompiledQuestion]:
    """Propose, phrase and run candidate questions; same inputs give identical output.

    Returns the accepted questions and every rejected candidate, in proposal order. At most
    min(max_questions, scene.questions.max_per_camera) are accepted.
    """
    if not scene.meta.settled:
        raise ValueError(f"{scene.meta.scene_id}: programs run on a settled scene only")
    cam = scene.camera(camera_id)
    rng = make_rng(seed)
    mix = scene.questions
    limit = min(max_questions, mix.max_per_camera)
    families = [f for f, spec in FAMILIES.items()
                if (mix.families is None or f in mix.families) and cam.rig in spec.rigs]
    proposer = _Proposer(scene, camera_id, rng)
    queues = {f: proposer.queue(f) for f in families}
    order = family_order(families, mix.weights, rng)
    view = proposer.view
    out: list[CompiledQuestion] = []
    accepted = 0

    def propose(cand: Candidate) -> bool:
        program, params, slots = cand
        family = PROGRAMS[program].family
        index = int(rng.integers(2))
        template_id, text, alt = phrase(program, index, rig=cam.rig, **slots)
        base = {
            "family": family,
            "template_id": template_id,
            "question": text,
            "question_alt": alt,
            "camera_id": camera_id,
            "program": ProgramRef(name=program, params=params),
        }
        try:
            result = run_program(program, params, scene, camera_id)
        except ProgramReject as rej:
            out.append(CompiledQuestion(**base, rejected=rej.reason, rejected_detail=rej.detail))
            return False
        hidden = hidden_evidence(view, result.evidence_object_ids, family, params)
        if hidden:
            out.append(CompiledQuestion(**base, rejected="evidence_hidden",
                                        rejected_detail={"objects": hidden}))  # fmt: skip
            return False
        out.append(CompiledQuestion(**base, result=result))
        return True

    for _ in range(ROUNDS):
        for family in order:
            if accepted >= limit:
                return out
            queue = queues[family]
            for _ in range(TRIES_PER_VISIT):
                if not queue:
                    break
                if propose(queue.pop(0)):
                    accepted += 1
                    break
    return out
