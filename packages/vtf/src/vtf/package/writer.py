"""Task package writer: tasks/<task_id>/{image.png, task.json}, manifest.jsonl, rejected.jsonl."""

import shutil
from pathlib import Path

from vtf import __version__ as VTF_VERSION
from vtf.core import layout
from vtf.core.hashing import sha256_json, short_id
from vtf.core.io import write_model
from vtf.core.program import CompiledQuestion
from vtf.core.render_manifest import RenderManifest
from vtf.core.scene import SceneSpec
from vtf.core.task import GateResult, ManifestRow, TaskPackage, Versions


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


def build_task(
    question: CompiledQuestion,
    settled: SceneSpec,
    scene_hash: str,
    gates: dict[str, GateResult],
    versions: Versions,
    difficulty: dict,
) -> TaskPackage:
    result = question.result
    assert result is not None
    return TaskPackage(
        task_id=make_task_id(scene_hash, question, result.answer, versions),
        scene_id=settled.meta.scene_id,
        scene_hash=scene_hash,
        camera_id=question.camera_id,
        question=question.question,
        family=question.family,
        template_id=question.template_id,
        program=question.program,
        answer=result.answer,
        answer_type=result.answer_type,
        answer_text=result.answer_text,
        tolerance=None,
        evidence_object_ids=result.evidence_object_ids,
        gates=gates,
        difficulty=difficulty,
        seed=settled.meta.seed,
        versions=versions,
    )


def write_task(lay: layout.RunLayout, task: TaskPackage, beauty: Path) -> ManifestRow:
    tdir = lay.task_dir(task.task_id)
    tdir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(beauty, tdir / layout.TASK_IMAGE)
    write_model(tdir / layout.TASK_FILE, task)
    return ManifestRow(
        task_id=task.task_id,
        scene_id=task.scene_id,
        camera_id=task.camera_id,
        family=task.family,
        answer_type=task.answer_type,
        path=f"{layout.TASKS_DIR}/{task.task_id}/{layout.TASK_FILE}",
    )
