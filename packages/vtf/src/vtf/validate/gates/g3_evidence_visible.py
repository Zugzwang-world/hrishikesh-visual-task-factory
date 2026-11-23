"""G3 Evidence visible (rule A): every object the answer depends on is visible enough.

Visible pixels come from the object-index pass; the visible fraction is visible pixels over the
object's amodal pixels (its full silhouette rendered alone). For count questions the evidence is
every matching object, so one hidden match rejects the question: nothing counted is hidden.
Counted objects in count families use the lower thresholds of spec/families.md (0.15 and 120 px);
every other evidence object (a container included) uses 0.4 and 300 px.

Scale in frame (rule A): distance and free_space need a visible scale, the table's printed grid
(table.grid_cell_m) or a procedural reference object (proc_* asset) passing the thresholds.
"""

from dataclasses import dataclass

import numpy as np

from vtf.core.scene import SceneSpec
from vtf.core.task import GateResult

MIN_VISIBLE_PIXELS = 300
MIN_VISIBLE_FRACTION = 0.4
COUNT_MIN_VISIBLE_PIXELS = 120
COUNT_MIN_VISIBLE_FRACTION = 0.15
COUNT_FAMILIES = ("count", "count_attribute")
SCALE_FAMILIES = ("distance", "free_space")
REFERENCE_PREFIX = "proc_"


@dataclass
class Visibility:
    visible_pixels: int
    amodal_pixels: int

    @property
    def fraction(self) -> float:
        return self.visible_pixels / self.amodal_pixels if self.amodal_pixels else 0.0


def visibility_table(
    index: np.ndarray, amodal: dict[int, np.ndarray], pass_indices: list[int]
) -> dict[int, Visibility]:
    """Visible and amodal pixel counts per pass index."""
    ids, counts = np.unique(index, return_counts=True)
    visible = dict(zip(ids.tolist(), counts.tolist(), strict=True))
    return {
        p: Visibility(
            visible_pixels=int(visible.get(p, 0)),
            amodal_pixels=int(amodal[p].sum()) if p in amodal else 0,
        )
        for p in pass_indices
    }


def thresholds(family: str | None, params: dict | None, object_id: str) -> tuple[int, float]:
    """(min pixels, min fraction) for one evidence object of a question."""
    params = params or {}
    if family in COUNT_FAMILIES and object_id != params.get("container"):
        return COUNT_MIN_VISIBLE_PIXELS, COUNT_MIN_VISIBLE_FRACTION
    return MIN_VISIBLE_PIXELS, MIN_VISIBLE_FRACTION


def scale_reference(scene: SceneSpec, table: dict[int, Visibility]) -> dict:
    """Is a metric scale visible in this camera? Grid on the table, or a visible proc_* object."""
    if scene.table.grid_cell_m:
        return {"ok": True, "kind": "grid", "grid_cell_m": scene.table.grid_cell_m}
    refs = []
    for o in scene.objects:
        if o.source == "asset" and o.shape.startswith(REFERENCE_PREFIX):
            vis = table.get(o.pass_index)
            if (
                vis
                and vis.visible_pixels >= MIN_VISIBLE_PIXELS
                and vis.fraction >= MIN_VISIBLE_FRACTION
            ):
                refs.append(o.id)
    return {"ok": bool(refs), "kind": "reference_object" if refs else None, "objects": refs}


def check_g3(
    evidence_object_ids: list[str],
    pass_index_of: dict[str, int],
    table: dict[int, Visibility],
    family: str | None = None,
    params: dict | None = None,
    scene: SceneSpec | None = None,
) -> GateResult:
    per_object = {}
    failing = []
    for oid in evidence_object_ids:
        px, frac = thresholds(family, params, oid)
        vis = table[pass_index_of[oid]]
        ok = vis.visible_pixels >= px and vis.fraction >= frac
        per_object[oid] = {
            "visible_pixels": vis.visible_pixels,
            "amodal_pixels": vis.amodal_pixels,
            "visible_fraction": round(vis.fraction, 4),
            "min_pixels": px,
            "min_fraction": frac,
            "pass": ok,
        }
        if not ok:
            failing.append(oid)
    evidence: dict = {"objects": per_object}
    if family in SCALE_FAMILIES and scene is not None:
        ref = scale_reference(scene, table)
        evidence["scale_reference"] = ref
        if not ref["ok"]:
            evidence["reason"] = "no_scale_in_frame"
            return GateResult(passed=False, evidence=evidence)
    if failing:
        evidence["reason"] = "evidence_not_visible"
        evidence["failing"] = failing
    return GateResult(passed=not failing, evidence=evidence)
