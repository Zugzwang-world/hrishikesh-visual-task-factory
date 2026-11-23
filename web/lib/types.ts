// Shapes of the files `vtf export --format explorer` writes (vtf.package.explorer, v0.1.0).
// Fields a run does not have are null, so everything optional is typed `| null`.

export type Vec3 = [number, number, number];
export type Quat = [number, number, number, number]; // w, x, y, z (Blender)
export type Mat3 = [Vec3, Vec3, Vec3];
export type Mat4 = number[][];

export interface ModelSummary {
  label: string;
  model: string | null;
  with_image: boolean | null;
  n: number;
  correct: number;
  accuracy: number | null;
}

export interface SceneSummary {
  scene_id: string;
  path: string;
  thumb: string | null;
  n_tasks: number;
  n_objects: number;
  families: Record<string, number>;
  cameras: { camera_id: string; rig: string | null }[];
  gltf: boolean;
  shortcut_conflicts: number;
  models: Record<string, { n: number; correct: number }>;
}

export interface ExplorerIndex {
  explorer_version: string;
  source: {
    run: string;
    recipe: string | null;
    run_seed: number | null;
    generator_version: string | null;
    spec_version: string | null;
    n_scenes: number;
    n_tasks: number;
  };
  families: string[];
  gates: string[];
  models: ModelSummary[];
  scenes: SceneSummary[];
}

export interface Pose {
  position: Vec3;
  rotation: Quat;
}

export interface CameraData {
  camera_id: string;
  rig: string | null;
  image: string | null;
  image_size: [number, number] | null;
  resolution: [number, number] | null;
  K: Mat3 | null;
  world_to_camera: Mat4 | null;
  focal_mm: number | null;
  sensor_width_mm: number | null;
}

export interface ObjectData {
  id: string;
  name: string;
  category: string | null;
  shape: string | null;
  source: string | null;
  color_name: string | null;
  size_word: string | null;
  rgb: Vec3 | null;
  position: Vec3 | null;
  rotation: Quat | null;
  dimensions: Vec3 | null;
  pass_index: number | null;
}

export interface GateData {
  pass: boolean;
  key: [string, string][];
  evidence: unknown;
}

export interface ModelAnswer {
  label: string;
  model: string | null;
  with_image: boolean | null;
  answer: unknown;
  parse_ok: boolean;
  correct: boolean;
  raw_response: string | null;
  error: string | null;
}

export interface TaskData {
  task_id: string;
  camera_id: string | null;
  rig: string | null;
  family: string;
  template_id: string | null;
  question: string;
  question_alt: string | null;
  answer: unknown;
  answer_type: string | null;
  answer_text: string;
  tolerance: number | null;
  shortcut_conflict: boolean | null;
  shortcut_answer: unknown;
  evidence_object_ids: string[];
  difficulty: Record<string, unknown>;
  gates: Record<string, GateData>;
  twin_of: string | null;
  twin_id: string | null;
  models: ModelAnswer[];
}

export interface GripperPart {
  name: string;
  shape: "box" | "cylinder";
  dimensions: Vec3;
  pose: Pose;
}

export interface ZoneData {
  id: string;
  shape: "rect" | "circle";
  center_xy: [number, number];
  size_xy: [number, number] | null;
  yaw_deg: number;
  radius: number | null;
  color_name: string | null;
  rgb: Vec3 | null;
}

export interface SceneData {
  scene_id: string;
  spec_version: string | null;
  seed: number | null;
  gltf: string | null;
  table: { size_xy: [number, number]; height: number; thickness: number; grid_cell_m?: number | null } | null;
  gripper: { tip: Pose | null; opening: number | null; parts: GripperPart[] };
  zones: ZoneData[];
  lighting: { hdri_id: string | null; condition: string | null };
  cameras: CameraData[];
  objects: ObjectData[];
  tasks: TaskData[];
}
