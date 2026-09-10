"""Embedding helpers. Vectors live in Postgres as REAL[]; cosine is computed
here in Python (volume is thousands of rows — no ANN index needed)."""
from __future__ import annotations

import math
from typing import Sequence


def parse_pg(v) -> list[float] | None:
    """A REAL[] column comes back from psycopg as a Python list already."""
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return [float(x) for x in v]
    try:
        return [float(x) for x in v.tolist()]  # numpy, just in case
    except AttributeError:
        pass
    s = str(v).strip().strip("[]{}")
    return [float(x) for x in s.split(",")] if s else None


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
