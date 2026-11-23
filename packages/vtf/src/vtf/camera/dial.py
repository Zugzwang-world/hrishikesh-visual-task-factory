"""The camera-quality dial: a seeded, post-render degradation of the beauty image, with lens
distortion applied alike to every pass so masks, index and depth stay aligned with the image.

Pure numpy and OpenCV; runs core side after Blender (Blender never sees the dial). Everything is
a pure function of the image, the CameraQuality values and K.

Order of operations on the beauty image (8-bit RGB as Blender wrote it):

1. Radial lens distortion (bilinear), zoomed to fill the frame (see below).
2. Gain (exposure) in linear light: gain < 1 is low light.
3. Gaussian blur (defocus, cheap optics), then linear motion blur at a seeded angle.
4. Gaussian sensor noise. Its sigma rises as gain drops: noise_sigma + LOW_LIGHT_SIGMA*(1/gain-1).
5. CCTV look: desaturate, crush blacks and clip highlights, slight green cast, vignette.
6. Quantise to 8 bits, then a JPEG round trip at jpeg_quality.

Distortion model: OpenCV's k1 term on normalised coordinates x = (u - cx) / fx, then a fill zoom.
A scene point that projects to undistorted (x, y) appears at

    (x, y) * s * (1 + k1 * r^2),   r^2 = x^2 + y^2,   s = 1 / (1 + k1 * r_b^2)

with r_b the half-diagonal of the frame for barrel distortion (k1 < 0) and the shorter half-side
for pincushion (k1 > 0). That s is the smallest zoom that keeps every output pixel inside the
rendered frame (like OpenCV's getOptimalNewCameraMatrix with alpha 0), so the image has no black
border: a barrel lens bulges and its edges compress, as a real wide lens does after cropping. K
in the manifest stays the pinhole K of the render; `distort_points(project(...), quality, K)`
is where a 3D point appears in the dialled image. The warp inverts the map per output pixel with
Newton iterations on the radius; pixels with no preimage (only past the fold of an extreme barrel)
are black, index 0, depth 0 and outside every amodal mask.

Pixel convention (vtf.core.projection): the centre of pixel (row i, column j) is (j + 0.5, i + 0.5)
in (u, v); a point (u, v) lies in pixel (floor(v), floor(u)).
"""

import functools

import cv2
import numpy as np

from vtf.core.scene import CameraQuality

LOW_LIGHT_SIGMA = 3.0  # extra noise sigma (8-bit levels) per unit of (1 / gain - 1)
CCTV_SATURATION = 0.25
CCTV_BLACK = 0.07  # values below this crush to black
CCTV_WHITE = 0.90  # values above this clip to white
CCTV_TINT = (0.94, 1.0, 0.95)
CCTV_VIGNETTE = 0.35  # brightness loss at the image corners
NEWTON_ITERATIONS = 30


# ------------------------------------------------------------------ helpers


def is_clean(q: CameraQuality) -> bool:
    """True when the dial leaves every pass untouched."""
    return (
        q.noise_sigma == 0
        and q.blur_px == 0
        and q.motion_blur_px == 0
        and q.distortion_k1 == 0
        and q.gain == 1.0
        and q.jpeg_quality is None
        and not q.cctv
    )


def effective_noise_sigma(q: CameraQuality) -> float:
    """Noise sigma in 8-bit levels after the low-light boost."""
    boost = LOW_LIGHT_SIGMA * (1.0 / q.gain - 1.0) if q.gain < 1.0 else 0.0
    return float(q.noise_sigma + boost)


def _k1(quality: CameraQuality | float) -> float:
    return float(quality.distortion_k1 if isinstance(quality, CameraQuality) else quality)


def _focal_and_centre(k: np.ndarray) -> tuple[float, float, float, float]:
    k = np.asarray(k, dtype=np.float64)
    return float(k[0, 0]), float(k[1, 1]), float(k[0, 2]), float(k[1, 2])


# ------------------------------------------------------------------ geometry


def fill_scale(quality: CameraQuality | float, k: np.ndarray) -> float:
    """The zoom s that makes the distorted image fill the frame (1.0 without distortion)."""
    k1 = _k1(quality)
    fx, fy, cx, cy = _focal_and_centre(k)
    hx, hy = cx / fx, cy / fy  # K's principal point is the image centre
    rb2 = hx * hx + hy * hy if k1 < 0 else min(hx, hy) ** 2
    denom = 1.0 + k1 * rb2
    return 1.0 / denom if denom > 0 else 1.0


def distort_points(uv: np.ndarray, quality: CameraQuality | float, k: np.ndarray) -> np.ndarray:
    """(N, 2) undistorted pixel coordinates (pinhole K) -> where they appear in the dialled
    image. Closed form."""
    k1 = _k1(quality)
    uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
    if k1 == 0:
        return uv.copy()
    fx, fy, cx, cy = _focal_and_centre(k)
    x = (uv[:, 0] - cx) / fx
    y = (uv[:, 1] - cy) / fy
    m = fill_scale(k1, k) * (1.0 + k1 * (x * x + y * y))
    return np.stack([x * m * fx + cx, y * m * fy + cy], axis=1)


def _undistort_normalised(xd: np.ndarray, yd: np.ndarray, k1: float, s: float):
    """Invert x_d = s x_u (1 + k1 r_u^2) per point: (x_u, y_u, valid)."""
    if np.size(xd) == 0:  # no points (an object with no pixels in this view): nothing to invert
        return xd.copy(), yd.copy(), np.zeros(np.shape(xd), dtype=bool)
    rd = np.hypot(xd, yd)
    target = rd / s
    ru = target.copy()
    for _ in range(NEWTON_ITERATIONS):
        f = ru * (1.0 + k1 * ru * ru) - target
        if np.abs(f).max() < 1e-13:
            break
        fp = 1.0 + 3.0 * k1 * ru * ru
        ru = ru - f / np.where(np.abs(fp) > 1e-12, fp, 1e-12)
    # Barrel distortion folds over beyond r^2 = 1 / (3 |k1|); such output pixels see nothing.
    residual = np.abs(ru * (1.0 + k1 * ru * ru) - target)
    valid = (residual < 1e-9) & (ru >= 0)
    if k1 < 0:
        valid &= 3.0 * k1 * ru * ru > -1.0
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = np.where(rd > 0, ru / rd, 1.0 / s)
    return xd * scale, yd * scale, valid


def undistort_points(uv: np.ndarray, quality: CameraQuality | float, k: np.ndarray) -> np.ndarray:
    """(N, 2) dialled-image pixel coordinates -> undistorted (pinhole K) ones; NaN where there is
    no preimage."""
    k1 = _k1(quality)
    uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
    if k1 == 0:
        return uv.copy()
    fx, fy, cx, cy = _focal_and_centre(k)
    xu, yu, valid = _undistort_normalised(
        (uv[:, 0] - cx) / fx, (uv[:, 1] - cy) / fy, k1, fill_scale(k1, k)
    )
    out = np.stack([xu * fx + cx, yu * fy + cy], axis=1)
    out[~valid] = np.nan
    return out


@functools.lru_cache(maxsize=8)
def _source_cached(h: int, w: int, k1: float, k_flat: tuple[float, ...]):
    k = np.asarray(k_flat, dtype=np.float64).reshape(3, 3)
    uu, vv = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    src = undistort_points(np.stack([uu.ravel(), vv.ravel()], axis=1), k1, k)
    su, sv = src[:, 0].reshape(h, w), src[:, 1].reshape(h, w)
    with np.errstate(invalid="ignore"):
        valid = np.isfinite(su) & (su >= 0) & (su < w) & (sv >= 0) & (sv < h)
    j = np.clip(np.floor(np.nan_to_num(su)).astype(np.int64), 0, w - 1)
    i = np.clip(np.floor(np.nan_to_num(sv)).astype(np.int64), 0, h - 1)
    for a in (su, sv, valid, i, j):
        a.setflags(write=False)  # shared through the cache
    return su, sv, valid, i, j


def source_coordinates(
    shape: tuple[int, int], quality: CameraQuality | float, k: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For every output pixel centre of an (H, W) image: the undistorted source (u, v) it shows
    (NaN where there is none), and whether that source lies inside the rendered frame."""
    k_flat = tuple(float(v) for v in np.asarray(k, dtype=np.float64).ravel())
    su, sv, valid, _, _ = _source_cached(int(shape[0]), int(shape[1]), _k1(quality), k_flat)
    return su, sv, valid


def warp_nearest(
    array: np.ndarray, quality: CameraQuality | float, k: np.ndarray, fill=0
) -> np.ndarray:
    """Nearest-neighbour lens warp of any per-pixel array (index, depth, mask): each output pixel
    takes the source pixel that contains its undistorted position; outside the frame is `fill`."""
    if _k1(quality) == 0:
        return array.copy()
    h, w = array.shape[:2]
    k_flat = tuple(float(v) for v in np.asarray(k, dtype=np.float64).ravel())
    _, _, valid, i, j = _source_cached(h, w, _k1(quality), k_flat)
    out = array[i, j]
    out[~valid] = fill
    return out


def warp_index(index: np.ndarray, quality: CameraQuality | float, k: np.ndarray) -> np.ndarray:
    """The dial's warp of an object-index image (e.g. a ray-cast index for the render-agreement
    check): exactly the warp applied to index.png."""
    return warp_nearest(index, quality, k, fill=0)


def warp_beauty(rgb: np.ndarray, quality: CameraQuality | float, k: np.ndarray) -> np.ndarray:
    """Bilinear lens warp of an 8-bit RGB image; black where the index warp says out of frame."""
    if _k1(quality) == 0:
        return rgb.copy()
    h, w = rgb.shape[:2]
    su, sv, valid = source_coordinates((h, w), quality, k)
    map_x = np.nan_to_num(su - 0.5).astype(np.float32)  # cv2 puts pixel centres on integers
    map_y = np.nan_to_num(sv - 0.5).astype(np.float32)
    out = cv2.remap(rgb, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    out[~valid] = 0
    return out


# ------------------------------------------------------------------ photometric


def _srgb_to_linear(x: np.ndarray) -> np.ndarray:
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def motion_kernel(length_px: float, angle_rad: float) -> np.ndarray:
    """Normalised line kernel of the given length and angle (anti-aliased, sub-pixel ends)."""
    half = int(np.ceil(length_px / 2.0)) + 1
    size = 2 * half + 1
    kernel = np.zeros((size, size), np.float32)
    shift = 4
    scale = 1 << shift
    dx, dy = np.cos(angle_rad) * length_px / 2.0, np.sin(angle_rad) * length_px / 2.0
    p0 = (round((half - dx) * scale), round((half - dy) * scale))
    p1 = (round((half + dx) * scale), round((half + dy) * scale))
    cv2.line(kernel, p0, p1, 1.0, thickness=1, lineType=cv2.LINE_AA, shift=shift)
    total = kernel.sum()
    if total <= 0:
        kernel[half, half] = 1.0
        total = 1.0
    return kernel / total


def _vignette(h: int, w: int, strength: float) -> np.ndarray:
    yy, xx = np.meshgrid(np.linspace(-1, 1, h), np.linspace(-1, 1, w), indexing="ij")
    r2 = (xx * xx + yy * yy) / 2.0  # 1 at the corners
    return (1.0 - strength * r2).astype(np.float32)[:, :, None]


def apply_to_beauty(rgb: np.ndarray, quality: CameraQuality, k: np.ndarray) -> np.ndarray:
    """The full dial on an (H, W, 3) uint8 RGB image. Deterministic for a given quality.seed."""
    q = quality
    if is_clean(q):
        return rgb.copy()
    rng = np.random.default_rng(q.seed)
    angle = float(rng.uniform(0.0, np.pi))  # drawn first, so it does not depend on other knobs
    img = warp_beauty(rgb, q, k)
    f = img.astype(np.float32) / 255.0
    if q.gain != 1.0:
        f = _linear_to_srgb(_srgb_to_linear(f) * q.gain).astype(np.float32)
    if q.blur_px > 0:
        f = cv2.GaussianBlur(f, (0, 0), sigmaX=q.blur_px, borderType=cv2.BORDER_REFLECT)
    if q.motion_blur_px > 0:
        kernel = motion_kernel(q.motion_blur_px, angle)
        f = cv2.filter2D(f, -1, kernel, borderType=cv2.BORDER_REFLECT)
    sigma = effective_noise_sigma(q)
    if sigma > 0:
        f = f + rng.normal(0.0, sigma / 255.0, size=f.shape).astype(np.float32)
    if q.cctv:
        f = np.clip(f, 0.0, 1.0)
        luma = f @ np.array([0.299, 0.587, 0.114], np.float32)
        f = luma[:, :, None] + CCTV_SATURATION * (f - luma[:, :, None])
        f = (f - CCTV_BLACK) / (CCTV_WHITE - CCTV_BLACK)
        f = f * np.array(CCTV_TINT, np.float32) * _vignette(*f.shape[:2], CCTV_VIGNETTE)
    out = np.clip(np.rint(f * 255.0), 0, 255).astype(np.uint8)
    if q.jpeg_quality is not None:
        ok, buf = cv2.imencode(
            ".jpg",
            cv2.cvtColor(out, cv2.COLOR_RGB2BGR),
            [cv2.IMWRITE_JPEG_QUALITY, int(q.jpeg_quality)],
        )
        if not ok:
            raise RuntimeError("JPEG encode failed")
        out = cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    return out
