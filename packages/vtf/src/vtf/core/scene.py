"""SceneSpec: the renderer-independent world state. Every answer is computed from this file.

Conventions (see also `vtf.core.geometry`):

- Metres; Blender's right-handed, Z-up world; quaternions w, x, y, z.
- The table is centred on the world origin in x and y; its top surface is the plane
  z = table.height.
- An object's `pose.position` is the centre of its local bounding box and `dimensions` are its
  full extents along its local x, y, z axes (after `scale`). Primitive meshes are centred on that
  point: box (dx, dy, dz); cylinder and cone with their axis along local z (dx = dy = diameter,
  dz = height; a cone's base is at local z = -dz/2 and its apex at +dz/2); sphere dx = dy = dz =
  diameter.
- Object pass indices are 1..999 and unique. The table renders with index 1000 and the gripper
  with 1001. Index 0 is background (floor, world).
- A camera's `pose.rotation` is the Blender camera rotation: it looks down its local -Z axis,
  local +Y is image up and +X is image right. Sensor fit is horizontal.
- Asset objects (`source = "asset"`, stage 3): `shape` is the asset id in assets/manifest.yaml
  (vtf.core.assets). The asset mesh is imported Z-up (glTF +Y up becomes +Z up), scaled by the
  manifest scale times `scale`, and centred on its bounding-box centre, so the same pose and
  dimensions conventions hold as for primitives. `vtf.core.assets.object_mesh` builds that mesh.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vtf.core.geometry import Pose, Vec2, Vec3, quat_multiply, transform_points

SPEC_VERSION = "0.2.0"

PRIMITIVE_SHAPES = ("box", "cylinder", "sphere", "cone")
CAMERA_RIGS = ("front", "overhead", "gripper")
SIZE_WORDS = ("small", "large")
TABLE_PASS_INDEX = 1000
GRIPPER_PASS_INDEX = 1001
MAX_OBJECT_PASS_INDEX = 999


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SceneMeta(_Model):
    spec_version: str = Field(default=SPEC_VERSION, description="Version of this scene format.")
    scene_id: str = Field(description="Stable scene name within a run, e.g. scene_0007.")
    generator_version: str = Field(description="Sampler name and version that produced the file.")
    seed: int = Field(
        ge=0, description="Per-scene seed; with the recipe it reproduces this file exactly."
    )
    recipe_hash: str = Field(description="sha256 of the recipe's canonical JSON.")
    settled: bool = Field(
        default=False,
        description="False as sampled; True once the renderer's settle wrote poses back.",
    )
    difficulty: dict[str, int | float | str | bool] = Field(
        default_factory=dict,
        description="Scene-level difficulty controls the sampler drew (clutter, look-alikes, "
        "stacks, lighting condition, ...). Copied into every task's difficulty vector.",
    )
    twin_of: str | None = Field(
        default=None,
        description="For a counterfactual twin (G6): the scene_id of the original scene.",
    )


class Table(_Model):
    size_xy: Vec2 = Field(description="Top size in metres along world x and y.")
    height: float = Field(gt=0, description="World z of the table's top surface, metres.")
    thickness: float = Field(default=0.03, gt=0, description="Top slab thickness, metres.")
    material_id: str = Field(
        default="wood_simple", description="Renderer material name: wood_simple or cm_grid."
    )
    grid_cell_m: float | None = Field(
        default=None,
        gt=0,
        description="When set, the table top shows a printed grid with this cell size (metres), "
        "lines on multiples of the cell from the table centre. A visible scale for cm questions.",
    )
    pass_index: int = Field(default=TABLE_PASS_INDEX)


class Placement(_Model):
    """How the sampler placed an object. The settled truth of support and containment is computed
    by programs from geometry; this records the sampler's intent (for the settle check)."""

    kind: Literal["table", "on", "in"] = "table"
    parent_id: str | None = Field(
        default=None, description="The object it was stacked on or placed in (kind on or in)."
    )


class SceneObject(_Model):
    id: str = Field(pattern=r"^obj_\d{2,}$", description="Stable id, e.g. obj_03.")
    source: Literal["primitive", "asset"] = Field(
        default="primitive", description="primitive in v0; asset for Poly Haven models (stage 3)."
    )
    shape: str = Field(
        description="Primitive name (box, cylinder, sphere, cone) or, for assets, the asset_id."
    )
    category: str = Field(
        description="Noun used in questions and answers; equals shape for primitives."
    )
    color_name: str | None = Field(description="Colour word used in questions, e.g. red.")
    size_word: Literal["small", "large"] | None = Field(
        default=None,
        description="Size word for look-alikes of one category (e.g. small mug, large mug).",
    )
    rgb: Vec3 = Field(description="Linear-light base colour, 0..1, as given to Principled BSDF.")
    roughness: float = Field(default=0.45, ge=0, le=1)
    metallic: float = Field(default=0.0, ge=0, le=1)
    dimensions: Vec3 = Field(description="Full extents along local x, y, z in metres.")
    scale: float = Field(default=1.0, gt=0, description="Uniform scale applied to an asset.")
    pose: Pose
    pass_index: int = Field(ge=1, le=MAX_OBJECT_PASS_INDEX, description="Object-index pass value.")
    placement: Placement = Field(default_factory=Placement)

    @property
    def name(self) -> str:
        """Full descriptive name, e.g. 'large red mug' (size and colour words when set)."""
        return " ".join(w for w in (self.size_word, self.color_name, self.category) if w)


class GripperPart(_Model):
    name: str
    shape: Literal["box", "cylinder"]
    dimensions: Vec3 = Field(description="Full extents along the part's local axes, metres.")
    pose: Pose = Field(description="World pose of the part's centre.")


class Gripper(_Model):
    """A static two-finger gripper. `parts` are derived from the parameters by `build_gripper`.

    Tip frame: origin at the midpoint between the fingertips; local +Z points up the arm (the
    approach direction is -Z); local x is the finger-opening axis.
    """

    tip: Pose
    opening: float = Field(gt=0, description="Gap between the fingers' inner faces, metres.")
    finger_length: float = Field(gt=0)
    finger_width: float = Field(gt=0, description="Finger size along tip-local y.")
    finger_thickness: float = Field(gt=0, description="Finger size along tip-local x.")
    palm_size: Vec3
    arm_length: float = Field(gt=0)
    arm_radius: float = Field(gt=0)
    material_id: str = "metal_grey"
    pass_index: int = Field(default=GRIPPER_PASS_INDEX)
    parts: list[GripperPart] = Field(description="Primitive parts in world coordinates.")


class Zone(_Model):
    """A marked region on the table top, painted as a coloured outline (reach zones, stage 3).

    Membership is decided on the table plane: an object is inside when its footprint (the
    projection of its mesh onto the table) lies inside the zone; the programs define the exact
    rule and the margin. The outline is drawn inside the zone boundary, `line_width_m` wide.
    """

    id: str
    kind: Literal["reach"] = "reach"
    shape: Literal["rect", "circle"]
    center_xy: Vec2
    size_xy: Vec2 | None = None
    yaw_deg: float = Field(default=0.0, description="Rect rotation about world z, degrees.")
    radius: float | None = None
    color_name: str | None = None
    rgb: Vec3 | None = None
    line_width_m: float = Field(default=0.012, gt=0)


class CameraQuality(_Model):
    """Post-render camera-quality dial (vtf.camera), seeded. Applied after Blender: noise and blur
    touch only the beauty image; lens distortion warps beauty, index, depth and masks alike."""

    level: Literal["clean", "mild", "harsh", "cctv"] = "clean"
    noise_sigma: float = Field(default=0.0, ge=0, description="Gaussian noise, 8-bit levels.")
    blur_px: float = Field(default=0.0, ge=0, description="Gaussian blur sigma, pixels.")
    motion_blur_px: float = Field(default=0.0, ge=0, description="Linear motion blur length.")
    distortion_k1: float = Field(
        default=0.0, description="Radial distortion k1 on normalised coordinates (barrel < 0)."
    )
    gain: float = Field(default=1.0, gt=0, description="Exposure multiplier; low light < 1.")
    jpeg_quality: int | None = Field(default=None, ge=1, le=100)
    cctv: bool = Field(default=False, description="Desaturated, contrast-crushed CCTV look.")
    seed: int = Field(default=0, ge=0)


class CameraSpec(_Model):
    id: str = Field(description="Camera id, e.g. front.")
    rig: Literal["front", "overhead", "gripper"] = Field(
        default="front",
        description="front, overhead or gripper. A gripper camera's pose is derived from the "
        "gripper tip by the sampler and stored here in world coordinates.",
    )
    pose: Pose = Field(description="Blender camera pose: looks down local -Z, local +Y is up.")
    focal_mm: float = Field(gt=0)
    sensor_width_mm: float = Field(default=36.0, gt=0, description="Sensor fit is horizontal.")
    resolution: tuple[int, int] = Field(description="Width, height in pixels.")
    clip_start: float = Field(default=0.01, gt=0)
    clip_end: float = Field(default=20.0, gt=0)
    quality: CameraQuality = Field(default_factory=CameraQuality)


class KeyLight(_Model):
    """A key light for crisp contact shadows (decision 1, brief 05); the HDRI stays as fill."""

    kind: Literal["sun", "area", "spot"] = "sun"
    elevation_deg: float = Field(ge=0, le=90, description="Angle above the horizon, degrees.")
    azimuth_deg: float = Field(description="Direction the light comes from, about world z.")
    strength: float = Field(gt=0, description="Sun: W/m^2. Area and spot: W.")
    angle_deg: float = Field(
        default=2.0, ge=0, description="Sun angular diameter; small gives crisp shadows."
    )
    size_m: float = Field(default=0.3, gt=0, description="Area light size (area only).")
    distance_m: float = Field(default=2.0, gt=0, description="Area and spot distance to centre.")
    rgb: Vec3 = (1.0, 1.0, 1.0)


class Lighting(_Model):
    hdri_id: str = Field(description="Poly Haven HDRI id, cached under assets/cache/<id>/.")
    strength: float = Field(default=1.0, gt=0)
    rotation_deg: float = Field(default=0.0, description="HDRI rotation about world Z, degrees.")
    key_light: KeyLight | None = None
    condition: str = Field(
        default="normal", description="Difficulty label: normal, dim, harsh or coloured."
    )


class QuestionMix(_Model):
    """Which question families the compiler may ask on this scene (from the recipe)."""

    families: list[str] | None = Field(default=None, description="None means every family.")
    max_per_camera: int = Field(default=5, gt=0)
    weights: dict[str, float] = Field(
        default_factory=dict, description="Optional family -> relative weight for proposals."
    )


class RenderSettings(_Model):
    engine: Literal["CYCLES"] = "CYCLES"
    samples: int = Field(default=128, gt=0)
    seed: int = Field(default=0, ge=0, description="Cycles sampling seed.")
    device: Literal["GPU", "CPU"] = Field(
        default="GPU", description="Default device; `vtf render --device` overrides."
    )
    denoise: bool = True


class Physics(_Model):
    """Rigid-body settle parameters. Part of the truth, because they decide the settled poses.

    Defaults follow the expert's measurements on Blender 5.3 (DECISIONS.md, 23 Nov 2026): 10
    substeps let cylinders fall through the table; 40 substeps and 20 iterations settle every
    tested scene by frame 57. Primitive collision shapes, Bullet deactivation on, split impulse
    on; the gripper and the table never move (the table is passive, the gripper is not simulated).
    """

    settle_frames: int = Field(
        default=90, ge=2, description="At least 2, so motion at the end can be measured."
    )
    fps: int = Field(default=24, gt=0)
    substeps_per_frame: int = Field(default=40, gt=0)
    solver_iterations: int = Field(default=20, gt=0)
    friction: float = Field(default=0.6, ge=0)
    restitution: float = Field(default=0.0, ge=0)
    linear_damping: float = Field(default=0.04, ge=0, le=1)
    angular_damping: float = Field(default=0.1, ge=0, le=1)
    collision_margin: float = Field(default=0.0005, ge=0)
    density_kg_m3: float = Field(default=600.0, gt=0)
    use_deactivation: bool = True
    use_split_impulse: bool = True


class SceneSpec(_Model):
    meta: SceneMeta
    table: Table
    objects: list[SceneObject]
    gripper: Gripper
    zones: list[Zone] = Field(default_factory=list)
    cameras: list[CameraSpec]
    lighting: Lighting
    render: RenderSettings = Field(default_factory=RenderSettings)
    physics: Physics = Field(default_factory=Physics)
    questions: QuestionMix = Field(default_factory=QuestionMix)

    @model_validator(mode="after")
    def _unique_ids(self) -> "SceneSpec":
        ids = [o.id for o in self.objects]
        if len(ids) != len(set(ids)):
            raise ValueError("object ids must be unique")
        indices = [o.pass_index for o in self.objects]
        if len(indices) != len(set(indices)):
            raise ValueError("object pass_index values must be unique")
        cams = [c.id for c in self.cameras]
        if len(cams) != len(set(cams)):
            raise ValueError("camera ids must be unique")
        known = set(ids)
        for o in self.objects:
            p = o.placement
            if (p.kind == "table") != (p.parent_id is None):
                raise ValueError(f"{o.id}: placement {p.kind} needs parent_id only for on/in")
            if p.parent_id is not None and p.parent_id not in known | {"table"}:
                raise ValueError(f"{o.id}: unknown placement parent {p.parent_id}")
        return self

    def object(self, object_id: str) -> SceneObject:
        for obj in self.objects:
            if obj.id == object_id:
                return obj
        raise KeyError(object_id)

    def camera(self, camera_id: str) -> CameraSpec:
        for cam in self.cameras:
            if cam.id == camera_id:
                return cam
        raise KeyError(camera_id)

    def object_by_pass_index(self) -> dict[int, SceneObject]:
        return {o.pass_index: o for o in self.objects}


def build_gripper(
    tip: Pose,
    opening: float = 0.05,
    finger_length: float = 0.055,
    finger_width: float = 0.018,
    finger_thickness: float = 0.010,
    palm_size: Vec3 = (0.09, 0.03, 0.025),
    arm_length: float = 0.25,
    arm_radius: float = 0.02,
) -> Gripper:
    """Build a gripper and its world-frame parts from the tip pose and the finger parameters."""
    px, py, pz = palm_size
    finger_x = opening / 2 + finger_thickness / 2
    local = [
        ("finger_left", "box", (finger_thickness, finger_width, finger_length),
         (-finger_x, 0.0, finger_length / 2)),
        ("finger_right", "box", (finger_thickness, finger_width, finger_length),
         (finger_x, 0.0, finger_length / 2)),
        ("palm", "box", (px, py, pz), (0.0, 0.0, finger_length + pz / 2)),
        ("arm", "cylinder", (2 * arm_radius, 2 * arm_radius, arm_length),
         (0.0, 0.0, finger_length + pz + arm_length / 2)),
    ]  # fmt: skip
    tip_matrix = tip.matrix()
    parts = []
    for name, shape, dims, offset in local:
        centre = transform_points(tip_matrix, [offset])[0]
        parts.append(
            GripperPart(
                name=name,
                shape=shape,
                dimensions=tuple(round(float(v), 6) for v in dims),
                pose=Pose(
                    position=tuple(round(float(v), 6) + 0.0 for v in centre),
                    rotation=quat_multiply(tip.rotation, (1.0, 0.0, 0.0, 0.0)),
                ),
            )
        )
    return Gripper(
        tip=tip,
        opening=opening,
        finger_length=finger_length,
        finger_width=finger_width,
        finger_thickness=finger_thickness,
        palm_size=palm_size,
        arm_length=arm_length,
        arm_radius=arm_radius,
        parts=parts,
    )


class RunInfo(_Model):
    """run.json at the root of a run folder, written by `vtf generate`."""

    run_version: str = "0.1.0"
    recipe: str = Field(description="Recipe path as given on the command line.")
    recipe_hash: str
    run_seed: int
    n: int
    generator_version: str
    scenes: dict[str, int] = Field(description="scene_id -> per-scene seed.")
