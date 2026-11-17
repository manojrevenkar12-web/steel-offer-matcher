"""Inventory search in Postgres: the same steps as the in-memory search, in SQL.

Hard filters in the WHERE clause; a keyword ranking from the full-text index (tsvector, GIN)
and a vector ranking from pgvector (HNSW, cosine) over the filtered pool; reciprocal rank
fusion of the two. A contract test runs the same queries against both implementations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..domain.fit import Fit, FitSettings, check
from ..domain.grades import GradeKB
from ..domain.spec import LineSpec, Policy, StockItem
from ..search.embed import embed, words
from ..search.inventory import SearchQuery
from .db import StockItemRow

_COLS = ["item_id", "supplier_id", "form", "finish", "grade_id", "grade_text", "coating", "surface", "thickness_mm", "width_mm",
         "length_mm", "weight_t", "price_eur_t", "country", "origin", "available_from", "certificate", "quality", "edge"]

SEARCH_SQL = text("""
WITH pool AS (
  SELECT item_id, tsv, embedding, price_eur_t FROM stock_items
  WHERE active
    AND (CAST(:finish AS text) IS NULL OR finish = :finish)
    AND (cardinality(CAST(:forms AS text[])) = 0 OR form = ANY(CAST(:forms AS text[])))
    AND (cardinality(CAST(:grades AS text[])) = 0 OR grade_id = ANY(CAST(:grades AS text[])) OR (:unresolved AND grade_id IS NULL))
    AND (CAST(:tmin AS float8) IS NULL OR thickness_mm >= CAST(:tmin AS float8) - 1e-9)
    AND (CAST(:tmax AS float8) IS NULL OR thickness_mm <= CAST(:tmax AS float8) + 1e-9)
    AND (CAST(:wmin AS float8) IS NULL OR width_mm >= CAST(:wmin AS float8) - 1e-9)
    AND NOT (item_id = ANY(CAST(:exclude AS text[])))
),
kw AS (
  SELECT item_id, row_number() OVER (ORDER BY ts_rank_cd(tsv, q) DESC, item_id) AS r
  FROM pool, to_tsquery('simple', :tsquery) q WHERE :tsquery <> '' AND tsv @@ q
),
vec AS (
  SELECT item_id, row_number() OVER (ORDER BY embedding <=> CAST(:qv AS vector), item_id) AS r FROM pool
),
fused AS (
  SELECT item_id, sum(1.0 / (:k + r)) AS score FROM (SELECT * FROM kw UNION ALL SELECT * FROM vec) x GROUP BY item_id
)
SELECT item_id FROM fused ORDER BY score DESC, item_id LIMIT :limit
""")

PLAIN_SQL = text("""
SELECT item_id FROM stock_items
WHERE active
  AND (CAST(:finish AS text) IS NULL OR finish = :finish)
  AND (cardinality(CAST(:forms AS text[])) = 0 OR form = ANY(CAST(:forms AS text[])))
  AND (cardinality(CAST(:grades AS text[])) = 0 OR grade_id = ANY(CAST(:grades AS text[])) OR (:unresolved AND grade_id IS NULL))
  AND (CAST(:tmin AS float8) IS NULL OR thickness_mm >= CAST(:tmin AS float8) - 1e-9)
  AND (CAST(:tmax AS float8) IS NULL OR thickness_mm <= CAST(:tmax AS float8) + 1e-9)
  AND (CAST(:wmin AS float8) IS NULL OR width_mm >= CAST(:wmin AS float8) - 1e-9)
  AND NOT (item_id = ANY(CAST(:exclude AS text[])))
ORDER BY price_eur_t, item_id LIMIT :limit
""")


def tsquery(q: str) -> str:
    toks = [re.sub(r"[^a-z0-9]", "", w) for w in words(q)]
    return " | ".join(dict.fromkeys(t for t in toks if t))


def to_item(row: StockItemRow) -> StockItem:
    return StockItem(**{c: getattr(row, c) for c in _COLS})


@dataclass
class PgInventory:
    sessions: async_sessionmaker
    rrf_k: int = 60

    async def add(self, items: list[StockItem], list_id: str | None = None) -> None:
        async with self.sessions() as s:
            for it in items:
                row = StockItemRow(**it.model_dump(), list_id=list_id or it.item_id.rsplit("-", 1)[0], active=True,
                                   search_text=it.search_text(), embedding=embed(it.search_text()))
                await s.merge(row)
            await s.commit()

    async def retire_supplier(self, supplier_id: str, keep_list: str) -> None:
        async with self.sessions() as s:
            await s.execute(update(StockItemRow).where(StockItemRow.supplier_id == supplier_id, StockItemRow.list_id != keep_list)
                            .values(active=False))
            await s.commit()

    async def clear(self) -> None:
        async with self.sessions() as s:
            await s.execute(delete(StockItemRow))
            await s.commit()

    async def get(self, item_id: str) -> StockItem | None:
        async with self.sessions() as s:
            row = await s.get(StockItemRow, item_id)
            return to_item(row) if row else None

    async def items(self, item_ids: list[str]) -> list[StockItem]:
        if not item_ids:
            return []
        async with self.sessions() as s:
            rows = (await s.execute(select(StockItemRow).where(StockItemRow.item_id.in_(item_ids)))).scalars().all()
        by_id = {r.item_id: to_item(r) for r in rows}
        return [by_id[i] for i in item_ids if i in by_id]

    async def search(self, q: SearchQuery) -> list[StockItem]:
        params = {"finish": q.finish, "forms": q.forms, "grades": q.grade_ids, "unresolved": q.include_unresolved,
                  "tmin": q.thickness_min_mm, "tmax": q.thickness_max_mm, "wmin": q.width_min_mm, "exclude": q.exclude_ids,
                  "limit": q.limit}
        async with self.sessions() as s:
            if q.text.strip():
                vec = "[" + ",".join(f"{x:.6f}" for x in embed(q.text)) + "]"
                ids = (await s.execute(SEARCH_SQL, params | {"tsquery": tsquery(q.text), "qv": vec, "k": self.rrf_k})).scalars().all()
            else:
                ids = (await s.execute(PLAIN_SQL, params)).scalars().all()
        return await self.items(list(ids))


@dataclass
class PgTools:
    inventory: PgInventory
    kb: GradeKB
    fit_settings: FitSettings
    today: date

    async def search(self, q: SearchQuery) -> list[StockItem]:
        return await self.inventory.search(q)

    async def items(self, item_ids: list[str]) -> list[StockItem]:
        return await self.inventory.items(item_ids)

    async def fits(self, spec: LineSpec, policy: Policy, item_ids: list[str]) -> list[Fit]:
        return [check(spec, policy, it, self.kb, self.fit_settings, self.today) for it in await self.items(item_ids)]
