"""SQLite cache of raw model replies, keyed by (task_id, model, prompt_version).

Task IDs are content hashes, so a cached reply is reused for the same task in any run folder.
Only replies that came back are stored; failed calls are retried on the next run. Use one
EvalCache per thread (the runner reads and writes it from the main thread only).
"""

import json
import os
import sqlite3
from pathlib import Path
from types import TracebackType

from vtf.eval.adapters.base import AdapterResponse

DEFAULT_CACHE = Path("out/cache/eval_cache.sqlite")
CACHE_ENV = "VTF_EVAL_CACHE"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS replies (
    task_id TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    latency_s REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    stop_reason TEXT,
    meta TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (task_id, model, prompt_version)
)
"""


def default_cache_path() -> Path:
    """$VTF_EVAL_CACHE, else out/cache/eval_cache.sqlite (relative to the working directory)."""
    return Path(os.environ.get(CACHE_ENV) or DEFAULT_CACHE)


class EvalCache:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_cache_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def get(self, task_id: str, model: str, prompt_version: str) -> AdapterResponse | None:
        row = self._conn.execute(
            "SELECT raw_text, latency_s, input_tokens, output_tokens, stop_reason, meta "
            "FROM replies WHERE task_id = ? AND model = ? AND prompt_version = ?",
            (task_id, model, prompt_version),
        ).fetchone()
        if row is None:
            return None
        raw_text, latency_s, input_tokens, output_tokens, stop_reason, meta = row
        return AdapterResponse(
            raw_text=raw_text,
            latency_s=latency_s,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=stop_reason,
            meta=json.loads(meta),
        )

    def put(self, task_id: str, model: str, prompt_version: str, response: AdapterResponse) -> None:
        if response.error is not None:
            raise ValueError("failed calls are not cached")
        self._conn.execute(
            "INSERT OR REPLACE INTO replies (task_id, model, prompt_version, raw_text, latency_s, "
            "input_tokens, output_tokens, stop_reason, meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                model,
                prompt_version,
                response.raw_text,
                response.latency_s,
                response.input_tokens,
                response.output_tokens,
                response.stop_reason,
                json.dumps(response.meta, sort_keys=True),
            ),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "EvalCache":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
