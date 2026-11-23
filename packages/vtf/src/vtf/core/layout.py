"""The run folder layout every stage reads and writes.

<run>/
  run.json                              RunInfo                      (vtf generate)
  scenes/<scene_id>/scene.json          SceneSpec, settled = false   (vtf generate)
  scenes/<scene_id>/render/             RenderManifest folder        (vtf render)
      render_manifest.json
      scene.settled.json
      <camera_id>/beauty.png, index.png, depth.npy, amodal/<pass_index>.png
  render_report.json                    per-scene status and seconds (vtf render)
  tasks/<task_id>/task.json, image.png  TaskPackage                  (vtf validate)
  manifest.jsonl                        ManifestRow per accepted task (vtf validate)
  rejected.jsonl                        RejectedRow per rejection    (vtf validate)
  validate_report.json                  gate pass rates              (vtf validate)
  eval/<model>/results.jsonl            EvalRecord per task          (vtf eval)
  eval/<model>/summary.json             accuracy per family          (vtf eval)
  verify/                               re-renders and verify report (vtf verify)
  twins/<twin_scene_id>/scene.json      G6 counterfactual twin scenes, same layout as scenes/
  twins/<twin_scene_id>/render/         (rendered like any scene; meta.twin_of names the original)
  logs/                                 long-run logs (scale run, eval)
  report/                               dataset report (P17), contact sheets
  baselines/                            G5 prior-only and question-only baseline results
  audit/                                blind audit sample and answers (P16)
  explorer/                             Task Explorer export (web)
"""

from pathlib import Path

RUN_FILE = "run.json"
SCENES_DIR = "scenes"
SCENE_FILE = "scene.json"
RENDER_DIR = "render"
RENDER_MANIFEST = "render_manifest.json"
SETTLED_SCENE = "scene.settled.json"
BEAUTY = "beauty.png"
INDEX = "index.png"
DEPTH = "depth.npz"
DEPTH_V01 = "depth.npy"
AMODAL_DIR = "amodal"
RENDER_REPORT = "render_report.json"
TASKS_DIR = "tasks"
TASK_FILE = "task.json"
TASK_IMAGE = "image.png"
TASK_MANIFEST = "manifest.jsonl"
REJECTED = "rejected.jsonl"
VALIDATE_REPORT = "validate_report.json"
EVAL_DIR = "eval"
EVAL_RESULTS = "results.jsonl"
EVAL_SUMMARY = "summary.json"
VERIFY_DIR = "verify"
TWINS_DIR = "twins"
LOGS_DIR = "logs"
REPORT_DIR = "report"
BASELINES_DIR = "baselines"
AUDIT_DIR = "audit"
EXPLORER_DIR = "explorer"
GLTF = "scene.glb"


def scene_id_for(index: int) -> str:
    return f"scene_{index:04d}"


class RunLayout:
    """Paths inside one run folder."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    @property
    def run_file(self) -> Path:
        return self.root / RUN_FILE

    @property
    def scenes_dir(self) -> Path:
        return self.root / SCENES_DIR

    def scene_ids(self) -> list[str]:
        if not self.scenes_dir.exists():
            return []
        return sorted(p.parent.name for p in self.scenes_dir.glob(f"*/{SCENE_FILE}"))

    def scene_dir(self, scene_id: str) -> Path:
        return self.scenes_dir / scene_id

    def scene_file(self, scene_id: str) -> Path:
        return self.scene_dir(scene_id) / SCENE_FILE

    def render_dir(self, scene_id: str) -> Path:
        return self.scene_dir(scene_id) / RENDER_DIR

    def render_manifest(self, scene_id: str) -> Path:
        return self.render_dir(scene_id) / RENDER_MANIFEST

    def settled_scene(self, scene_id: str) -> Path:
        return self.render_dir(scene_id) / SETTLED_SCENE

    @property
    def render_report(self) -> Path:
        return self.root / RENDER_REPORT

    @property
    def tasks_dir(self) -> Path:
        return self.root / TASKS_DIR

    def task_dir(self, task_id: str) -> Path:
        return self.tasks_dir / task_id

    @property
    def task_manifest(self) -> Path:
        return self.root / TASK_MANIFEST

    @property
    def rejected(self) -> Path:
        return self.root / REJECTED

    @property
    def validate_report(self) -> Path:
        return self.root / VALIDATE_REPORT

    def eval_dir(self, model: str) -> Path:
        return self.root / EVAL_DIR / model.replace("/", "_")

    @property
    def verify_dir(self) -> Path:
        return self.root / VERIFY_DIR

    @property
    def twins_dir(self) -> Path:
        return self.root / TWINS_DIR

    def twin_layout(self) -> "RunLayout":
        """Twin scenes use the same layout as scenes/, rooted at <run>/twins/ (scenes_dir)."""
        return _TwinLayout(self.root)

    @property
    def logs_dir(self) -> Path:
        return self.root / LOGS_DIR

    @property
    def report_dir(self) -> Path:
        return self.root / REPORT_DIR

    @property
    def baselines_dir(self) -> Path:
        return self.root / BASELINES_DIR

    @property
    def audit_dir(self) -> Path:
        return self.root / AUDIT_DIR

    @property
    def explorer_dir(self) -> Path:
        return self.root / EXPLORER_DIR


class _TwinLayout(RunLayout):
    @property
    def scenes_dir(self) -> Path:
        return self.root / TWINS_DIR
