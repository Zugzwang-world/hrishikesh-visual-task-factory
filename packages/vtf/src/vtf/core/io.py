"""Reading and writing contract files. JSON is pretty-printed with a trailing newline, in model
field order, so the same model always gives the same bytes."""

import json
from collections.abc import Iterable, Iterator
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel

from vtf.core.render_manifest import RenderManifest
from vtf.core.scene import SceneSpec
from vtf.core.task import TaskPackage


def model_json(model: BaseModel) -> str:
    data = model.model_dump(mode="json", by_alias=True)
    return json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def write_model(path: Path, model: BaseModel) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model_json(model), encoding="utf-8", newline="\n")
    return path


def read_model[M: BaseModel](path: Path, cls: type[M]) -> M:
    return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


def read_scene(path: Path) -> SceneSpec:
    return read_model(path, SceneSpec)


def read_manifest(path: Path) -> RenderManifest:
    return read_model(path, RenderManifest)


def read_task(path: Path) -> TaskPackage:
    return read_model(path, TaskPackage)


def write_jsonl(path: Path, rows: Iterable[BaseModel | dict]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            data = row.model_dump(mode="json", by_alias=True) if isinstance(row, BaseModel) else row
            f.write(json.dumps(data, ensure_ascii=False, allow_nan=False) + "\n")
    return path


def read_jsonl(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def read_index_png(path: Path) -> np.ndarray:
    """16-bit object-index image -> (H, W) uint16."""
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(path)
    if img.dtype != np.uint16 or img.ndim != 2:
        raise ValueError(f"{path}: expected single-channel 16-bit PNG, got {img.dtype} {img.shape}")
    return img


def write_index_png(path: Path, index: np.ndarray) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.ascontiguousarray(index.astype(np.uint16)))


def read_mask_png(path: Path) -> np.ndarray:
    """Amodal mask PNG -> (H, W) bool."""
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(path)
    if img.ndim == 3:
        img = img[:, :, 0]
    return img > 0


def write_mask_png(path: Path, mask: np.ndarray) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.where(mask, 255, 0).astype(np.uint8))


def read_depth(path: Path) -> np.ndarray:
    depth = np.load(path, allow_pickle=False)
    if depth.dtype != np.float32 or depth.ndim != 2:
        raise ValueError(f"{path}: expected (H, W) float32, got {depth.dtype} {depth.shape}")
    return depth


def read_rgb_png(path: Path) -> np.ndarray:
    """8-bit RGB image -> (H, W, 3) uint8 in RGB order."""
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def write_rgb_png(path: Path, rgb: np.ndarray) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(
        str(path), cv2.cvtColor(np.ascontiguousarray(rgb, dtype=np.uint8), cv2.COLOR_RGB2BGR)
    )
