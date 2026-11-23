"""RenderManifest: what the renderer writes for each scene.

The renderer (packages/vtf-blender, inside Blender) writes, per scene, into one output folder:

    render_manifest.json      this model
    scene.settled.json        the SceneSpec with settled poses and meta.settled = true
    <camera_id>/beauty.png    8-bit RGB
    <camera_id>/index.png     16-bit single channel; pixel value = pass_index, 0 = background
                              (from a 1-sample CPU render, so it is exact on re-render)
    <camera_id>/depth.npy     float32 (H, W); planar camera-frame depth in metres of whatever the
                              ray hits, 0.0 = no hit. Floor and table legs are renderer props
                              outside the scene file: index 0, but they keep their depth.
    <camera_id>/amodal/<pass_index>.png   8-bit single channel; 255 where the object, rendered
                                          alone, covers the pixel; 0 elsewhere

All paths in the manifest are relative to the manifest's folder. The core reads only PNG and
npy files, so it never needs an EXR reader or bpy.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MANIFEST_VERSION = "0.1.0"

Matrix3 = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
Matrix4 = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CameraOutputs(_Model):
    camera_id: str
    resolution: tuple[int, int] = Field(description="Width, height in pixels.")
    beauty: str = Field(description="Relative path to beauty.png.")
    index: str = Field(description="Relative path to the 16-bit index.png.")
    depth: str = Field(
        description="Relative path to depth.npy (float32, metres; floor and legs keep depth)."
    )
    depth_kind: Literal["planar_z"] = Field(
        default="planar_z", description="Depth is camera-frame z (along the optical axis)."
    )
    amodal: dict[str, str] = Field(
        description="str(pass_index) -> relative path of that object's amodal mask PNG."
    )
    K: Matrix3 = Field(description="3x3 intrinsics, OpenCV convention, pixel origin top-left.")
    world_to_camera: Matrix4 = Field(
        description="4x4 world -> OpenCV camera frame (x right, y down, z forward)."
    )
    render_seconds: float = Field(ge=0)
    amodal_seconds: float = Field(ge=0)


class SettleReport(_Model):
    frames: int
    substeps_per_frame: int
    seconds: float = Field(ge=0)
    displacement_m: dict[str, float] = Field(
        description="object id -> distance between spawn and settled position, metres."
    )
    max_displacement_m: float
    off_table: list[str] = Field(
        default_factory=list, description="Objects whose settled centre left the table top."
    )
    moving_at_end: list[str] = Field(
        default_factory=list, description="Objects still moving between the last two frames."
    )
    support_gap_m: dict[str, float] = Field(
        default_factory=dict,
        description="object id -> lowest point minus the table top, metres (about 0 at rest).",
    )


class RenderManifest(_Model):
    manifest_version: str = MANIFEST_VERSION
    scene_id: str
    scene_file: str = Field(description="Path of the input scene file as given to the renderer.")
    scene_sha256: str = Field(description="sha256 of the input scene file's bytes.")
    settled_scene_file: str = Field(default="scene.settled.json")
    renderer: str = Field(default="blender", description="blender, or fixture for test data.")
    blender_version: str
    device: Literal["GPU", "CPU"] = Field(description="Device actually used for beauty.")
    data_passes_device: Literal["GPU", "CPU"] = Field(
        default="CPU",
        description="Device for index, depth and amodal passes. CPU makes them exact on re-render.",
    )
    device_name: str = ""
    samples: int
    seed: int
    denoise: bool
    cameras: list[CameraOutputs]
    settle: SettleReport
    timings: dict[str, float] = Field(
        description="Seconds: build, settle, render, amodal, total (wall clock inside Blender)."
    )

    def camera(self, camera_id: str) -> CameraOutputs:
        for cam in self.cameras:
            if cam.camera_id == camera_id:
                return cam
        raise KeyError(camera_id)
