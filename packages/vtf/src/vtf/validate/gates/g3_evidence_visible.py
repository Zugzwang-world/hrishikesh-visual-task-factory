"""G3 Evidence visible (rule A): every object the answer depends on is visible enough.

Visible pixels come from the object-index pass; the visible fraction is visible pixels over the
object's amodal pixels (its full silhouette rendered alone). For count questions the evidence is
every matching object, so one hidden match rejects the question: nothing counted is hidden.
"""

from dataclasses import dataclass

import numpy as np

from vtf.core.task import GateResult

MIN_VISIBLE_PIXELS = 300
MIN_VISIBLE_FRACTION = 0.4


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


def check_g3(
    evidence_object_ids: list[str],
    pass_index_of: dict[str, int],
    table: dict[int, Visibility],
    min_pixels: int = MIN_VISIBLE_PIXELS,
    min_fraction: float = MIN_VISIBLE_FRACTION,
) -> GateResult:
    per_object = {}
    failing = []
    for oid in evidence_object_ids:
        vis = table[pass_index_of[oid]]
        ok = vis.visible_pixels >= min_pixels and vis.fraction >= min_fraction
        per_object[oid] = {
            "visible_pixels": vis.visible_pixels,
            "amodal_pixels": vis.amodal_pixels,
            "visible_fraction": round(vis.fraction, 4),
            "pass": ok,
        }
        if not ok:
            failing.append(oid)
    evidence: dict = {
        "min_visible_pixels": min_pixels,
        "min_visible_fraction": min_fraction,
        "objects": per_object,
    }
    if failing:
        evidence["reason"] = "evidence_not_visible"
        evidence["failing"] = failing
    return GateResult(passed=not failing, evidence=evidence)
