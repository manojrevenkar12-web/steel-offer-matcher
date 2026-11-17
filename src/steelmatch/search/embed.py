"""A small deterministic text embedder for designations and stock descriptions.

Character trigrams and word tokens are hashed into a fixed number of dimensions. It needs no
model download and gives the same vectors in Python and in Postgres (pgvector), which keeps the
in-memory and database searches identical. A hosted embedding model can replace it behind the
same function; the hybrid search does not change.
"""

from __future__ import annotations

import hashlib
import math
import re

DIM = 256


def _tokens(text: str) -> list[str]:
    t = text.lower()
    words = re.findall(r"[a-z]+\d*[a-z\d]*|\d+(?:[.,]\d+)?", t)
    grams = []
    for w in words:
        padded = f"#{w}#"
        grams.extend(padded[i:i + 3] for i in range(len(padded) - 2))
    return words + grams


def _bucket(tok: str) -> tuple[int, float]:
    h = hashlib.blake2b(tok.encode(), digest_size=8).digest()
    idx = int.from_bytes(h[:4], "little") % DIM
    sign = 1.0 if h[4] & 1 else -1.0
    return idx, sign


def embed(text: str) -> list[float]:
    v = [0.0] * DIM
    for tok in _tokens(text):
        i, s = _bucket(tok)
        v[i] += s
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [round(x / n, 6) for x in v]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def words(text: str) -> list[str]:
    return re.findall(r"[a-z]+\d*[a-z\d]*|\d+(?:[.,]\d+)?", text.lower())
