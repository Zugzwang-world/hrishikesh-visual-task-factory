"""Camera-quality presets for the sampler: clean, mild, harsh, cctv.

Each function returns a CameraQuality with fixed parameters; `seed` seeds the noise pattern and
the motion-blur direction. Give every camera its own seed (e.g. derived from the scene seed and
the camera id) so two cameras of one scene do not share a noise pattern.
"""

from collections.abc import Callable

from vtf.core.scene import CameraQuality


def clean(seed: int = 0) -> CameraQuality:
    """No degradation: the render as Blender wrote it."""
    return CameraQuality(level="clean", seed=seed)


def mild(seed: int = 0) -> CameraQuality:
    """A decent webcam: light noise and softness, slight barrel distortion, JPEG 85."""
    return CameraQuality(
        level="mild",
        noise_sigma=2.0,
        blur_px=0.6,
        motion_blur_px=0.0,
        distortion_k1=-0.04,
        gain=0.9,
        jpeg_quality=85,
        seed=seed,
    )


def harsh(seed: int = 0) -> CameraQuality:
    """A cheap camera in poor light: low gain, noise, blur, motion blur, strong barrel, JPEG 45."""
    return CameraQuality(
        level="harsh",
        noise_sigma=5.0,
        blur_px=1.2,
        motion_blur_px=5.0,
        distortion_k1=-0.12,
        gain=0.55,
        jpeg_quality=45,
        seed=seed,
    )


def cctv(seed: int = 0) -> CameraQuality:
    """Security-camera look: wide barrel lens, desaturated, crushed contrast, vignette, JPEG 35.
    No text overlays."""
    return CameraQuality(
        level="cctv",
        noise_sigma=4.0,
        blur_px=1.0,
        motion_blur_px=0.0,
        distortion_k1=-0.18,
        gain=0.8,
        jpeg_quality=35,
        cctv=True,
        seed=seed,
    )


PRESETS: dict[str, Callable[[int], CameraQuality]] = {
    "clean": clean,
    "mild": mild,
    "harsh": harsh,
    "cctv": cctv,
}


def preset(level: str, seed: int = 0) -> CameraQuality:
    """The named preset (clean, mild, harsh or cctv) with this seed."""
    try:
        return PRESETS[level](seed)
    except KeyError:
        raise ValueError(
            f"unknown camera-quality level {level!r}; have {sorted(PRESETS)}"
        ) from None
