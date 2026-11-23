"""Recipe: the YAML file that, with a seed, decides every sampled scene.

Every number the sampler uses lives here, not in code. Ranges are [low, high] pairs, sampled
uniformly. The recipe hash is the sha256 of the validated recipe (defaults filled in), so two
recipes that sample the same scenes hash the same.

Two recipe formats: `Recipe` (version 0.1.0, sampler v0: coloured primitives, one front camera)
and `RecipeV1` (version 0.2.0, `sampler: v1`: assets, stacks, containers, zones, look-alikes,
duplicates, a low gripper, three cameras, key light). `load_recipe` picks by the `sampler` key.
"""

from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from vtf.core.hashing import sha256_json
from vtf.core.program import FAMILIES
from vtf.core.scene import (
    CAMERA_RIGS,
    PRIMITIVE_SHAPES,
    CameraQuality,
    Physics,
    QuestionMix,
    RenderSettings,
)
from vtf.core.vocab import COLOURS

RECIPE_VERSION = "0.1.0"


def _ordered[T: tuple](value: T) -> T:
    if value[0] > value[1]:
        raise ValueError(f"range low {value[0]} is above high {value[1]}")
    return value


FloatRange = Annotated[tuple[float, float], AfterValidator(_ordered)]
IntRange = Annotated[tuple[int, int], AfterValidator(_ordered)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TableRecipe(_Model):
    size_xy: tuple[float, float] = Field(description="Top size along world x and y, metres.")
    height: float = Field(gt=0, description="World z of the table top, metres.")
    thickness: float = Field(default=0.03, gt=0)
    material_id: str = "wood_simple"


class ObjectsRecipe(_Model):
    count: IntRange = Field(description="Objects per scene, inclusive.")
    shapes: list[str] = Field(description="Primitive shapes to draw from.")
    colours: list[str] = Field(description="Colour names from vtf.core.vocab.COLOURS.")
    size_m: FloatRange = Field(
        description="Box sides; cylinder and cone diameter and height; sphere diameter."
    )
    box_yaw_deg: FloatRange = Field(description="Box rotation about world z, degrees.")
    drop_m: float = Field(ge=0, description="Spawn height of the object's base above the table.")
    roughness: float = Field(default=0.45, ge=0, le=1)
    metallic: float = Field(default=0.0, ge=0, le=1)

    @model_validator(mode="after")
    def _known_words(self) -> Self:
        unknown = [s for s in self.shapes if s not in PRIMITIVE_SHAPES]
        unknown += [c for c in self.colours if c not in COLOURS]
        if unknown:
            raise ValueError(f"unknown shapes or colours: {unknown}")
        if len(set(self.shapes)) * len(set(self.colours)) < self.count[1]:
            raise ValueError("not enough unique colour and shape pairs for count[1] objects")
        if self.count[0] < 1:
            raise ValueError("count must be at least 1")
        return self


class PlacementRecipe(_Model):
    table_margin_m: float = Field(
        ge=0, description="Footprint circles stay this far inside the table edge."
    )
    gap_m: float = Field(ge=0, description="Minimum gap between two footprint circles.")
    tries_per_object: int = Field(gt=0, description="Position draws per object before restart.")


class GripperRecipe(_Model):
    tip_height_m: FloatRange = Field(description="Tip height above the table top.")
    xy_margin_m: float = Field(ge=0, description="Tip x, y stay this far inside the table edge.")
    yaw_deg: FloatRange = Field(description="Rotation about world z, degrees.")
    min_clearance_m: float = Field(
        ge=0, description="Fingertips stay this far above the tallest object's resting top."
    )
    tries: int = Field(gt=0, description="Pose draws before the layout restarts.")


class CameraRecipe(_Model):
    id: str = "front"
    rig: str = "front"
    target_jitter_m: float = Field(
        ge=0, description="Look-at target is the table-top centre plus uniform x, y jitter."
    )
    distance_m: FloatRange = Field(description="Eye distance from the target.")
    elevation_deg: FloatRange = Field(description="Tilt down from horizontal.")
    azimuth_deg: FloatRange = Field(
        description="Rotation about world z; 0 puts the camera on the -y side looking at +y."
    )
    focal_mm: FloatRange
    sensor_width_mm: float = Field(default=36.0, gt=0)
    resolution: tuple[int, int] = Field(description="Width, height in pixels.")
    view_margin_px: float = Field(
        ge=0, description="Projected object and gripper corners stay this far inside the image."
    )
    clip_start: float = Field(default=0.01, gt=0)
    clip_end: float = Field(default=20.0, gt=0)


class LightingRecipe(_Model):
    hdri_id: str
    strength: float = Field(default=1.0, gt=0)
    rotation_deg: FloatRange = Field(description="HDRI rotation about world z, degrees.")


class Recipe(_Model):
    recipe_version: str = RECIPE_VERSION
    name: str
    description: str = ""
    table: TableRecipe
    objects: ObjectsRecipe
    placement: PlacementRecipe
    gripper: GripperRecipe
    camera: CameraRecipe
    lighting: LightingRecipe
    render: RenderSettings = Field(default_factory=RenderSettings)
    physics: Physics = Field(
        default_factory=Physics, description="Settle parameters; omit to use the core defaults."
    )
    max_layout_attempts: int = Field(
        gt=0, description="Whole-layout restarts before sampling gives up (deterministic)."
    )

    @property
    def recipe_hash(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


# Recipe v1 (sampler v1) ---------------------------------------------------------------------

RECIPE_V1_VERSION = "0.2.0"
CLUTTER_LEVELS = ("low", "medium", "high")
QUALITY_LEVELS = ("clean", "mild", "harsh", "cctv")
LIGHTING_CONDITIONS = ("normal", "dim", "harsh", "coloured")

# Camera-quality values per level, until vtf.camera ships its own presets. Every value is a
# CameraQuality field; a recipe may override any of them.
DEFAULT_QUALITY_PRESETS: dict[str, dict[str, float | int | bool]] = {
    "clean": {},
    "mild": {"noise_sigma": 2.0, "blur_px": 0.5, "distortion_k1": -0.03, "jpeg_quality": 85},
    "harsh": {"noise_sigma": 6.0, "blur_px": 1.2, "motion_blur_px": 4.0, "distortion_k1": -0.08,
              "gain": 0.6, "jpeg_quality": 60},
    "cctv": {"noise_sigma": 5.0, "blur_px": 1.0, "distortion_k1": -0.15, "gain": 0.85,
             "jpeg_quality": 40, "cctv": True},
}  # fmt: skip


def _check_colours(colours: list[str]) -> list[str]:
    unknown = [c for c in colours if c not in COLOURS]
    if unknown:
        raise ValueError(f"unknown colours: {unknown}")
    return colours


Colours = Annotated[list[str], AfterValidator(_check_colours)]


class TableRecipeV1(_Model):
    size_xy: tuple[float, float] = Field(description="Top size along world x and y, metres.")
    height: float = Field(gt=0, description="World z of the table top, metres.")
    thickness: float = Field(default=0.03, gt=0)
    material_id: str = "wood_simple"
    grid_fraction: float = Field(
        default=0.0, ge=0, le=1, description="Share of scenes whose top shows the cm grid."
    )
    grid_material_id: str = "cm_grid"
    grid_cell_m: float = Field(default=0.01, gt=0)


class ClutterLevel(_Model):
    count: IntRange = Field(description="Objects per scene at this level, inclusive.")
    weight: float = Field(default=1.0, ge=0)


class AssetPoolRecipe(_Model):
    """Which manifest entries may be placed (vtf.core.assets). None means no filter."""

    categories: list[str] | None = None
    ids: list[str] | None = None
    exclude_ids: list[str] = Field(default_factory=list)
    source_weights: dict[Literal["polyhaven", "procedural"], float] = Field(
        default_factory=lambda: {"polyhaven": 1.0, "procedural": 1.0},
        description="Relative draw weight of each source (split evenly among its entries).",
    )


class PrimitivePoolRecipe(_Model):
    """Stage-2 style coloured primitives of random size (source primitive, category = shape)."""

    shapes: list[str] = Field(default_factory=lambda: list(PRIMITIVE_SHAPES))
    size_m: FloatRange = (0.04, 0.10)
    roughness: float = Field(default=0.45, ge=0, le=1)
    metallic: float = Field(default=0.0, ge=0, le=1)

    @model_validator(mode="after")
    def _known_shapes(self) -> Self:
        unknown = [s for s in self.shapes if s not in PRIMITIVE_SHAPES]
        if unknown:
            raise ValueError(f"unknown primitive shapes: {unknown}")
        return self


class ObjectsRecipeV1(_Model):
    clutter: dict[str, ClutterLevel] = Field(
        description="Clutter level (low, medium, high) -> object count range and draw weight."
    )
    pool_weights: dict[Literal["assets", "primitives"], float] = Field(
        default_factory=lambda: {"assets": 1.0, "primitives": 0.0},
        description="Relative draw weight of manifest assets and random primitives.",
    )
    assets: AssetPoolRecipe = Field(default_factory=AssetPoolRecipe)
    primitives: PrimitivePoolRecipe = Field(default_factory=PrimitivePoolRecipe)
    colours: Colours = Field(description="Colour words for primitives and procedural assets.")
    yaw_deg: FloatRange = Field(default=(-180.0, 180.0), description="Rotation about world z.")
    drop_m: float = Field(default=0.01, ge=0, description="Spawn gap above the support.")
    fill_ratio: float = Field(
        default=0.35, gt=0, le=1, description="Table-level footprint area budget / table area."
    )

    @model_validator(mode="after")
    def _levels(self) -> Self:
        unknown = [k for k in self.clutter if k not in CLUTTER_LEVELS]
        if unknown or not self.clutter:
            raise ValueError(f"clutter levels must be among {CLUTTER_LEVELS}, got {unknown}")
        if min(lv.count[0] for lv in self.clutter.values()) < 1:
            raise ValueError("clutter counts must be at least 1")
        if sum(lv.weight for lv in self.clutter.values()) <= 0:
            raise ValueError("clutter weights must not all be zero")
        if sum(self.pool_weights.values()) <= 0:
            raise ValueError("pool weights must not all be zero")
        return self


class PlacementRecipeV1(_Model):
    table_margin_m: float = Field(ge=0, description="Footprints stay this far inside the edge.")
    gap_m: float = Field(ge=0, description="Minimum gap between two footprint rectangles.")
    tries_per_object: int = Field(gt=0, description="Position draws per object before restart.")


class StacksRecipe(_Model):
    count: IntRange = Field(default=(0, 0), description="Two-high stacks per scene.")
    max_offset_frac: float = Field(
        default=0.2, ge=0, lt=1, description="Top centre offset / support half extent."
    )
    max_size_ratio: float = Field(
        default=1.2, gt=0,
        description="Top's largest horizontal extent / support's smallest horizontal extent.",
    )  # fmt: skip
    yaw_jitter_deg: float = Field(default=15.0, ge=0, description="Top yaw = support yaw +- this.")


class ContainersRecipe(_Model):
    count: IntRange = Field(default=(0, 0), description="Containers per scene.")
    items: IntRange = Field(default=(0, 2), description="Objects placed in each container.")
    wall_margin_m: float = Field(default=0.005, ge=0, description="Gap to the interior wall.")
    max_height_ratio: float = Field(
        default=1.5, gt=0, description="Item height / container depth (rim - floor)."
    )
    max_fill: float = Field(
        default=0.5, gt=0, le=1, description="Sum of item footprint areas / interior area."
    )


class LookalikesRecipe(_Model):
    colour_pairs: IntRange = Field(
        default=(0, 0), description="Pairs of different categories with the same colour."
    )
    size_pairs: IntRange = Field(
        default=(0, 0), description="Pairs of one asset at two sizes (small and large)."
    )
    small_scale: FloatRange = (0.68, 0.72)
    large_scale: FloatRange = (1.30, 1.40)


class DuplicatesRecipe(_Model):
    probability: float = Field(
        default=0.0, ge=0, le=1, description="Chance a scene has one group of identical objects."
    )
    copies: IntRange = Field(default=(2, 3), description="Objects in the group.")


class ZonesRecipe(_Model):
    count: IntRange = Field(default=(0, 0), description="Reach zones per scene.")
    colours: Colours = Field(default_factory=lambda: list(COLOURS))
    rect_weight: float = Field(default=0.5, ge=0, le=1, description="Chance a zone is a rect.")
    rect_size_m: FloatRange = (0.18, 0.35)
    circle_radius_m: FloatRange = (0.09, 0.17)
    yaw_deg: FloatRange = (-45.0, 45.0)
    line_width_m: float = Field(default=0.012, gt=0)
    boundary_margin_m: float = Field(
        default=0.02, ge=0, description="Object centres stay this far from the zone boundary."
    )
    min_inside: int = Field(default=1, ge=0)
    min_outside: int = Field(default=1, ge=0)
    tries: int = Field(default=200, gt=0)


class GripperRecipeV1(_Model):
    clearance_m: FloatRange = Field(
        description="Tip height above the tallest resting object within `neighbourhood_m`."
    )
    neighbourhood_m: float = Field(default=0.15, gt=0, description="xy radius for `clearance_m`.")
    over_object_probability: float = Field(
        default=0.8, ge=0, le=1, description="Chance the tip is placed over a random object."
    )
    over_object_offset_m: float = Field(default=0.05, ge=0)
    xy_margin_m: float = Field(ge=0, description="Tip x, y stay this far inside the table edge.")
    yaw_deg: FloatRange = Field(description="Rotation about world z, degrees.")
    min_part_gap_m: float = Field(
        default=0.008, ge=0, description="Spawned objects stay this far below the fingertips."
    )
    tries: int = Field(gt=0)


class FrontCameraRecipe(_Model):
    target_jitter_m: float = Field(ge=0)
    distance_m: FloatRange
    elevation_deg: FloatRange
    azimuth_deg: FloatRange
    focal_mm: FloatRange


class OverheadCameraRecipe(_Model):
    height_m: FloatRange = Field(description="Eye height above the table top.")
    focal_mm: FloatRange
    xy_jitter_m: float = Field(default=0.02, ge=0)
    tilt_deg: FloatRange = Field(default=(0.0, 3.0), description="Off straight down.")
    roll_deg: FloatRange = Field(default=(-4.0, 4.0), description="About the optical axis.")


class GripperCameraRecipe(_Model):
    hfov_deg: FloatRange = Field(default=(70.0, 90.0), description="Horizontal field of view.")
    mount_gap_m: float = Field(
        default=0.01, ge=0, description="Camera centre this far beside the palm's +y face."
    )


class QualityRecipe(_Model):
    weights: dict[str, float] = Field(default_factory=lambda: {"clean": 1.0})
    per_rig: dict[str, dict[str, float]] = Field(
        default_factory=dict, description="Optional rig -> level weights override."
    )
    presets: dict[str, dict[str, float | int | bool]] = Field(
        default_factory=lambda: {k: dict(v) for k, v in DEFAULT_QUALITY_PRESETS.items()}
    )

    @model_validator(mode="after")
    def _levels(self) -> Self:
        for weights in (self.weights, *self.per_rig.values()):
            unknown = [k for k in weights if k not in QUALITY_LEVELS]
            if unknown or sum(weights.values()) <= 0:
                raise ValueError(f"quality weights need levels among {QUALITY_LEVELS}")
        if any(r not in CAMERA_RIGS for r in self.per_rig):
            raise ValueError(f"per_rig keys must be among {CAMERA_RIGS}")
        for level in QUALITY_LEVELS:
            CameraQuality(level=level, **self.presets.get(level, {}))  # type: ignore[arg-type]
        return self


class CamerasRecipe(_Model):
    rigs: list[Literal["front", "overhead", "gripper"]] = Field(
        default_factory=lambda: ["front", "overhead", "gripper"]
    )
    resolution: tuple[int, int] = Field(description="Width, height in pixels.")
    sensor_width_mm: float = Field(default=36.0, gt=0)
    view_margin_px: float = Field(
        ge=0, description="Objects, gripper and zones stay this far inside front and overhead."
    )
    clip_start: float = Field(default=0.01, gt=0)
    clip_end: float = Field(default=20.0, gt=0)
    front: FrontCameraRecipe
    overhead: OverheadCameraRecipe
    gripper: GripperCameraRecipe = Field(default_factory=GripperCameraRecipe)
    quality: QualityRecipe = Field(default_factory=QualityRecipe)


class LightingConditionRecipe(_Model):
    weight: float = Field(default=1.0, ge=0)
    hdri_strength: FloatRange
    key_strength: FloatRange
    key_elevation_deg: FloatRange
    key_angle_deg: FloatRange = (1.0, 3.0)
    key_rgb: list[tuple[float, float, float]] = Field(default_factory=lambda: [(1.0, 1.0, 1.0)])


class LightingRecipeV1(_Model):
    hdri_ids: list[str] = Field(min_length=1)
    rotation_deg: FloatRange
    key_azimuth_deg: FloatRange = (0.0, 360.0)
    conditions: dict[str, LightingConditionRecipe]

    @model_validator(mode="after")
    def _conditions(self) -> Self:
        unknown = [k for k in self.conditions if k not in LIGHTING_CONDITIONS]
        if unknown or sum(c.weight for c in self.conditions.values()) <= 0:
            raise ValueError(f"lighting conditions must be among {LIGHTING_CONDITIONS}")
        return self


class RecipeV1(_Model):
    recipe_version: str = RECIPE_V1_VERSION
    sampler: Literal["v1"]
    name: str
    description: str = ""
    asset_manifest: str | None = Field(
        default=None,
        description="Asset manifest path relative to the repo root; None: the default manifest "
        "($VTF_ASSET_MANIFEST or assets/manifest.yaml).",
    )
    table: TableRecipeV1
    objects: ObjectsRecipeV1
    placement: PlacementRecipeV1
    stacks: StacksRecipe = Field(default_factory=StacksRecipe)
    containers: ContainersRecipe = Field(default_factory=ContainersRecipe)
    lookalikes: LookalikesRecipe = Field(default_factory=LookalikesRecipe)
    duplicates: DuplicatesRecipe = Field(default_factory=DuplicatesRecipe)
    zones: ZonesRecipe = Field(default_factory=ZonesRecipe)
    gripper: GripperRecipeV1
    cameras: CamerasRecipe
    lighting: LightingRecipeV1
    questions: QuestionMix = Field(default_factory=QuestionMix)
    render: RenderSettings = Field(default_factory=RenderSettings)
    physics: Physics = Field(default_factory=Physics)
    max_layout_attempts: int = Field(gt=0)

    @model_validator(mode="after")
    def _families(self) -> Self:
        unknown = [f for f in self.questions.families or [] if f not in FAMILIES]
        if unknown:
            raise ValueError(f"unknown question families: {unknown}")
        return self

    @property
    def recipe_hash(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


AnyRecipe = Recipe | RecipeV1


def load_recipe(path: Path | str) -> AnyRecipe:
    """Load a recipe; `sampler: v1` selects RecipeV1, otherwise the v0 Recipe."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if isinstance(data, dict) and data.get("sampler") == "v1":
        return RecipeV1.model_validate(data)
    return Recipe.model_validate(data)
