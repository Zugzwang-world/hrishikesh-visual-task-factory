"""Seeded randomness. Every generator draws from numpy's Generator seeded here; nothing else."""

import numpy as np

from vtf.core.hashing import canonical_json, sha256_bytes


def derive_seed(*parts: int | str) -> int:
    """A stable 63-bit seed from any mix of ints and strings, e.g. derive_seed(run_seed, 7)."""
    return int(sha256_bytes(canonical_json(list(parts)))[:16], 16) & ((1 << 63) - 1)


def make_rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)
