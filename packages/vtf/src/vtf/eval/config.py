"""Resolve a --model value to a ModelSpec using configs/models.yaml.

--model is a key from models.yaml (its settings, price and pinned model_id apply), a model_id
listed there, a fake-* adapter, or a bare claude-* id (Anthropic, default settings, no price).
The key is what results are filed and cached under, so to compare two settings of one model
(say two effort levels), give each its own key.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from vtf.eval.adapters.fake import FAKES

DEFAULT_CONFIG = Path("configs/models.yaml")
UNPINNED = {"", "TBD"}


class Price(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: float = Field(description="USD per million input tokens.")
    output: float = Field(description="USD per million output tokens.")


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    provider: str
    model_id: str
    verified: bool = False
    price_usd_per_mtok: Price | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    concurrency: int = 4


def find_config(path: Path | str | None = None) -> Path | None:
    """The given path, else configs/models.yaml in the working directory or the repo."""
    if path is not None:
        return Path(path)
    for root in (Path.cwd(), *Path(__file__).resolve().parents):
        candidate = root / DEFAULT_CONFIG
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def resolve_model(model: str, config_path: Path | str | None = None) -> ModelSpec:
    config = load_config(find_config(config_path))
    defaults = dict(config.get("defaults") or {})
    concurrency = int(defaults.pop("concurrency", 4))

    if model in FAKES:
        return ModelSpec(key=model, provider="fake", model_id=model, verified=True,
                         price_usd_per_mtok=Price(input=0, output=0), concurrency=1)  # fmt: skip

    entries = config.get("models") or []
    entry = next((e for e in entries if e.get("key") == model), None)
    if entry is None:
        entry = next((e for e in entries if e.get("model_id") == model), None)
    if entry is None:
        if not model.startswith("claude-"):
            raise ValueError(
                f"unknown model {model!r}: add it to configs/models.yaml "
                f"(or use one of {', '.join(sorted(FAKES))})"
            )
        entry = {"key": model, "provider": "anthropic", "model_id": model}
    if str(entry.get("model_id", "")).strip() in UNPINNED:
        raise ValueError(f"model {model!r} has no pinned model_id in configs/models.yaml")

    settings = {**defaults, **(entry.get("settings") or {})}
    return ModelSpec(**{"concurrency": concurrency, **entry, "key": model, "settings": settings})
