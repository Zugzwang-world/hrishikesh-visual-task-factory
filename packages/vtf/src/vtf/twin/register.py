"""G8 registration stub: find the mat's ArUco markers in a photo and map the photo onto the mat.

`register_photo` detects the four corner markers (OpenCV DICT_4X4_50, ids 0 to 3) and fits the
homography from photo pixels to the mat plane in metres (the scene's world x, y on the table top;
see `vtf.twin.mat`). Its inverse projects any layout point (an object's footprint, a centre) into
the photo, which is what the G8 legibility check overlays for the human. All 16 marker corners
take part in the fit; `rms_mm` is the corner residual on the mat after the fit.

Only points on the table plane map exactly; object tops sit above it, so overlays use footprints.
"""

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from vtf.twin.mat import ARUCO_DICT, MatSpec, default_mat_spec

Matrix3 = list[list[float]]


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = Field(description="Enough markers found and the fit residual is small.")
    marker_ids: list[int] = Field(description="Mat marker ids found in the photo, sorted.")
    image_size: tuple[int, int] = Field(description="Photo width, height in pixels.")
    photo_to_mat: Matrix3 | None = Field(
        default=None, description="3x3 homography: photo pixel (u, v, 1) -> mat metres (x, y, 1)."
    )
    mat_to_photo: Matrix3 | None = Field(default=None, description="Inverse of photo_to_mat.")
    rms_mm: float | None = Field(default=None, description="Marker-corner residual on the mat.")
    reason: str = ""


def _detector() -> cv2.aruco.ArucoDetector:
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(ARUCO_DICT), params)


def detect_markers(image: np.ndarray) -> dict[int, np.ndarray]:
    """Marker id -> (4, 2) float corners in photo pixels (OpenCV order: TL, TR, BR, BL)."""
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = _detector().detectMarkers(gray)
    if ids is None:
        return {}
    found: dict[int, np.ndarray] = {}
    for c, marker_id in zip(corners, ids.ravel(), strict=True):
        found.setdefault(int(marker_id), c.reshape(4, 2).astype(np.float64))
    return found


def map_points(h: np.ndarray | Matrix3, points: np.ndarray) -> np.ndarray:
    """Apply a 3x3 homography to (N, 2) points."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, np.asarray(h, dtype=np.float64)).reshape(-1, 2)


def register_photo(
    image: np.ndarray,
    spec: MatSpec | None = None,
    min_markers: int = 3,
    max_rms_mm: float = 3.0,
) -> Registration:
    """Detect the mat markers in a photo (BGR or grey) and fit photo -> mat."""
    spec = spec or default_mat_spec()
    height, width = image.shape[:2]
    known = {m.id: np.asarray(m.corners_xy, dtype=np.float64) for m in spec.markers}
    found = {k: v for k, v in detect_markers(image).items() if k in known}
    ids = sorted(found)
    if len(ids) < max(min_markers, 1):
        return Registration(
            ok=False,
            marker_ids=ids,
            image_size=(width, height),
            reason=f"found {len(ids)} of {len(known)} mat markers, need {min_markers}",
        )
    photo_pts = np.concatenate([found[i] for i in ids])
    mat_pts = np.concatenate([known[i] for i in ids])
    h, _ = cv2.findHomography(photo_pts, mat_pts, method=0)
    if h is None:
        return Registration(
            ok=False, marker_ids=ids, image_size=(width, height), reason="homography fit failed"
        )
    residual = map_points(h, photo_pts) - mat_pts
    rms_mm = float(np.sqrt(np.mean(np.sum(residual**2, axis=1)))) * 1000
    ok = rms_mm <= max_rms_mm
    return Registration(
        ok=ok,
        marker_ids=ids,
        image_size=(width, height),
        photo_to_mat=h.tolist(),
        mat_to_photo=np.linalg.inv(h).tolist(),
        rms_mm=round(rms_mm, 4),
        reason="" if ok else f"corner residual {rms_mm:.2f} mm above {max_rms_mm} mm",
    )
