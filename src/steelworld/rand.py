"""Keyed randomness: the same key gives the same draw in every design, so designs face the same luck."""

from __future__ import annotations

import hashlib
import random


def of(seed: int, *key: object) -> random.Random:
    h = hashlib.sha256(("|".join([str(seed), *map(str, key)])).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "little"))


def weighted(r: random.Random, table: dict[str, float]) -> str:
    items = [(k, v) for k, v in table.items() if not k.startswith("_")]
    total = sum(v for _, v in items)
    x = r.random() * total
    for k, v in items:
        x -= v
        if x <= 0:
            return k
    return items[-1][0]
