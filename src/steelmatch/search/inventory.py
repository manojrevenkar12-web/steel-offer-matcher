"""Inventory search: hard filters first, then keyword and vector rankings fused with RRF.

Hard filters (finish, form family, grade set, thickness window, minimum width, excluded ids)
are applied by code and are never relaxed by the ranking. Inside the filtered set, a BM25
keyword ranking and a cosine ranking over the embedder are merged with reciprocal rank fusion.
The Postgres implementation in service/search.py runs the same steps in SQL; a contract test
keeps the two in step.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from ..domain.spec import StockItem
from .embed import cosine, embed, words


class SearchQuery(BaseModel):
    text: str = ""
    finish: str | None = None
    forms: list[str] = Field(default_factory=list)
    grade_ids: list[str] = Field(default_factory=list)
    include_unresolved: bool = False
    thickness_min_mm: float | None = None
    thickness_max_mm: float | None = None
    width_min_mm: float | None = None
    exclude_ids: list[str] = Field(default_factory=list)
    limit: int = 20


def passes(q: SearchQuery, it: StockItem) -> bool:
    if q.finish and it.finish != q.finish:
        return False
    if q.forms and it.form not in q.forms:
        return False
    if q.grade_ids and it.grade_id not in q.grade_ids and not (q.include_unresolved and it.grade_id is None):
        return False
    if q.thickness_min_mm is not None and it.thickness_mm < q.thickness_min_mm - 1e-9:
        return False
    if q.thickness_max_mm is not None and it.thickness_mm > q.thickness_max_mm + 1e-9:
        return False
    if q.width_min_mm is not None and it.width_mm < q.width_min_mm - 1e-9:
        return False
    if it.item_id in q.exclude_ids:
        return False
    return True


def rrf(rankings: list[list[str]], k: int) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for pos, item_id in enumerate(ranking):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + pos + 1)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


@dataclass
class MemoryInventory:
    items: dict[str, StockItem] = field(default_factory=dict)
    vectors: dict[str, list[float]] = field(default_factory=dict)
    terms: dict[str, Counter] = field(default_factory=dict)
    rrf_k: int = 60

    def add(self, items: list[StockItem]) -> None:
        for it in items:
            self.items[it.item_id] = it
            text = it.search_text()
            self.vectors[it.item_id] = embed(text)
            self.terms[it.item_id] = Counter(words(text))

    def remove(self, item_ids: list[str]) -> None:
        for i in item_ids:
            self.items.pop(i, None)
            self.vectors.pop(i, None)
            self.terms.pop(i, None)

    def get(self, item_id: str) -> StockItem | None:
        return self.items.get(item_id)

    def search(self, q: SearchQuery) -> list[StockItem]:
        pool = [it for it in self.items.values() if passes(q, it)]
        if not pool:
            return []
        if not q.text.strip():
            pool.sort(key=lambda it: (it.price_eur_t, it.item_id))
            return pool[: q.limit]
        qv = embed(q.text)
        qt = words(q.text)
        vec_rank = [it.item_id for it in sorted(pool, key=lambda it: (-cosine(qv, self.vectors[it.item_id]), it.item_id))]
        kw = self._bm25(qt, [it.item_id for it in pool])
        kw_rank = [i for i, s in kw if s > 0]
        fused = rrf([kw_rank, vec_rank], self.rrf_k)
        return [self.items[i] for i, _ in fused[: q.limit]]

    def _bm25(self, qt: list[str], ids: list[str], k1: float = 1.2, b: float = 0.75) -> list[tuple[str, float]]:
        n = len(ids)
        avg = sum(sum(self.terms[i].values()) for i in ids) / max(1, n)
        df = Counter()
        for i in ids:
            for t in set(self.terms[i]):
                df[t] += 1
        out = []
        for i in ids:
            tf = self.terms[i]
            length = sum(tf.values())
            s = 0.0
            for t in qt:
                if tf[t] == 0:
                    continue
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * length / max(1e-9, avg)))
            out.append((i, s))
        out.sort(key=lambda kv: (-kv[1], kv[0]))
        return out
