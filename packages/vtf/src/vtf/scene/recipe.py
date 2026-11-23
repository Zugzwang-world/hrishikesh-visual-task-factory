"""Recipe: the YAML file that, with a seed, decides every sampled scene.

Every number the sampler uses lives here, not in code. Ranges are [low, high] pairs, sampled
uniformly. The recipe hash is the sha256 of the validated recipe (defaults filled in), so two
recipes that sample the same scenes hash the same.
"""

from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from vtf.core.hashing import sha256_json
from vtf.core.scene import PRIMITIVE_SHAPES, Physics, RenderSettings
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


def load_recipe(path: Path | str) -> Recipe:
    with open(path, encoding="utf-8") as f:
        return Recipe.model_validate(yaml.safe_load(f))
