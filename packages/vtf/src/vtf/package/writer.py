"""Task package writer: tasks/<task_id>/{image.png, task.json} and their manifest rows.

The image is a hard link to the render's beauty.png (vtf.core.io.link_or_copy), so a task costs
almost no disk. scene_objects lists every scene object with its naming words, so the scorer can
resolve a free-text answer; the name is the object's unique name under the naming rule of
spec/families.md (its full name when it has none).
"""

from pathlib import Path
from typing import Any

from vtf import __version__ as VTF_VERSION
from vtf.core import layout
from vtf.core.hashing import sha256_json, short_id
from vtf.core.io import link_or_copy, write_model
from vtf.core.program import CompiledQuestion
from vtf.core.render_manifest import RenderManifest
from vtf.core.scene import SceneSpec
from vtf.core.task import GateResult, ManifestRow, ObjectRef, TaskPackage, Versions
from vtf.validate.names import display_name


def task_versions(settled: SceneSpec, manifest: RenderManifest, programs_version: str) -> Versions:
    return Versions(
        vtf=VTF_VERSION,
        spec=settled.meta.spec_version,
        generator=settled.meta.generator_version,
        programs=programs_version,
        renderer=f"{manifest.renderer} {manifest.blender_version}",
    )


def make_task_id(
    scene_hash: str, question: CompiledQuestion, answer: object, versions: Versions
) -> str:
    """Content hash of everything that defines the task (not of timings or gate evidence)."""
    return short_id(
        "t",
        sha256_json(
            {
                "scene_hash": scene_hash,
                "camera_id": question.camera_id,
                "template_id": question.template_id,
                "question": question.question,
                "program": question.program.model_dump(mode="json"),
                "answer": answer,
                "versions": versions.model_dump(mode="json"),
            }
        ),
    )


def scene_object_refs(scene: SceneSpec) -> list[ObjectRef]:
    return [
        ObjectRef(
            id=o.id,
            name=display_name(o, scene),
            category=o.category,
            color_name=o.color_name,
            size_word=o.size_word,
        )
        for o in sorted(scene.objects, key=lambda o: o.id)
    ]


def _flat(value: Any) -> int | float | str:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float | str):
        return value
    return str(value)


def task_difficulty(
    settled: SceneSpec,
    question: CompiledQuestion,
    rig: str,
    min_visible_fraction: float,
) -> dict[str, int | float | str]:
    """Scene difficulty + question difficulty + shortcut_conflict + margins + min visible
    fraction + camera quality level (bools become 0/1: TaskPackage.difficulty has no bool)."""
    result = question.result
    assert result is not None
    cam = settled.camera(question.camera_id)
    out: dict[str, int | float | str] = {k: _flat(v) for k, v in settled.meta.difficulty.items()}
    out.update({k: _flat(v) for k, v in result.difficulty.items()})
    out["n_objects"] = len(settled.objects)
    out["rig"] = rig
    out["camera_quality"] = cam.quality.level
    out["min_visible_fraction"] = round(float(min_visible_fraction), 4)
    if result.shortcut_conflict is not None:
        out["shortcut_conflict"] = int(result.shortcut_conflict)
    if result.margin is not None:
        out["margin_m"] = round(float(result.margin), 4)
    if result.margin_px is not None:
        out["margin_px"] = round(float(result.margin_px), 2)
    return out


def number_tolerance(question: CompiledQuestion) -> float | None:
    result = question.result
    assert result is not None
    if result.answer_type != "number":
        return result.tolerance
    if result.tolerance is not None:
        return result.tolerance
    return round(max(2.0, 0.1 * abs(float(result.answer))), 4)  # spec/families.md, distance


def build_task(
    question: CompiledQuestion,
    settled: SceneSpec,
    scene_hash: str,
    gates: dict[str, GateResult],
    versions: Versions,
    difficulty: dict,
    rig: str = "front",
    twin_of: str | None = None,
    twin_id: str | None = None,
) -> TaskPackage:
    result = question.result
    assert result is not None
    return TaskPackage(
        task_id=make_task_id(scene_hash, question, result.answer, versions),
        scene_id=settled.meta.scene_id,
        scene_hash=scene_hash,
        camera_id=question.camera_id,
        rig=rig,
        question=question.question,
        question_alt=question.question_alt,
        family=question.family,
        template_id=question.template_id,
        program=question.program,
        answer=result.answer,
        answer_type=result.answer_type,
        answer_text=result.answer_text,
        tolerance=number_tolerance(question),
        shortcut_conflict=result.shortcut_conflict,
        shortcut_answer=result.shortcut_answer,
        evidence_object_ids=result.evidence_object_ids,
        scene_objects=scene_object_refs(settled),
        gates=gates,
        twin_of=twin_of,
        twin_id=twin_id,
        difficulty=difficulty,
        seed=settled.meta.seed,
        versions=versions,
    )


def manifest_row(task: TaskPackage) -> ManifestRow:
    return ManifestRow(
        task_id=task.task_id,
        scene_id=task.scene_id,
        camera_id=task.camera_id,
        rig=task.rig,
        family=task.family,
        answer_type=task.answer_type,
        shortcut_conflict=task.shortcut_conflict,
        path=f"{layout.TASKS_DIR}/{task.task_id}/{layout.TASK_FILE}",
    )


def write_task(lay: layout.RunLayout, task: TaskPackage, beauty: Path) -> ManifestRow:
    tdir = lay.task_dir(task.task_id)
    tdir.mkdir(parents=True, exist_ok=True)
    link_or_copy(beauty, tdir / layout.TASK_IMAGE)
    write_model(tdir / layout.TASK_FILE, task)
    return manifest_row(task)
