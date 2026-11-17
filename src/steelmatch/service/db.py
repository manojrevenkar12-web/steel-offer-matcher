"""Database tables (SQLAlchemy 2.0, async) for the service. Alembic owns the schema."""

from __future__ import annotations

import os
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, BigInteger, Boolean, Computed, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..search.embed import DIM

Json = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class StockListRow(Base):
    __tablename__ = "stock_lists"
    list_id: Mapped[str] = mapped_column(String, primary_key=True)
    supplier_id: Mapped[str] = mapped_column(String, index=True)
    filename: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)  # loaded | held
    problems: Mapped[list] = mapped_column(Json, default=list)
    items: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    used_model: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LayoutRow(Base):
    __tablename__ = "layouts"
    fingerprint: Mapped[str] = mapped_column(String, primary_key=True)
    mapping: Mapped[dict] = mapped_column(Json)
    approved_by: Mapped[str] = mapped_column(String)  # "validated" | a person's id
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StockItemRow(Base):
    __tablename__ = "stock_items"
    item_id: Mapped[str] = mapped_column(String, primary_key=True)
    list_id: Mapped[str] = mapped_column(String, ForeignKey("stock_lists.list_id"), index=True)
    supplier_id: Mapped[str] = mapped_column(String, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    form: Mapped[str] = mapped_column(String)
    finish: Mapped[str] = mapped_column(String)
    grade_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    grade_text: Mapped[str] = mapped_column(String)
    coating: Mapped[str | None] = mapped_column(String, nullable=True)
    surface: Mapped[str | None] = mapped_column(String, nullable=True)
    thickness_mm: Mapped[float] = mapped_column(Float)
    width_mm: Mapped[float] = mapped_column(Float)
    length_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_t: Mapped[float] = mapped_column(Float)
    price_eur_t: Mapped[float] = mapped_column(Float)
    country: Mapped[str] = mapped_column(String(2))
    origin: Mapped[str | None] = mapped_column(String(2), nullable=True)
    available_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    certificate: Mapped[str] = mapped_column(String(4))
    quality: Mapped[str] = mapped_column(String)
    edge: Mapped[str] = mapped_column(String)
    search_text: Mapped[str] = mapped_column(Text)
    tsv = mapped_column(TSVECTOR, Computed("to_tsvector('simple', search_text)", persisted=True))
    embedding = mapped_column(Vector(DIM))


class RfqRow(Base):
    __tablename__ = "rfqs"
    message_id: Mapped[str] = mapped_column(String, primary_key=True)
    received: Mapped[str] = mapped_column(String)
    sender: Mapped[str] = mapped_column(String)
    subject: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, index=True)  # queued | ready | review | not_rfq | sent | failed
    raw: Mapped[dict] = mapped_column(Json)
    result: Mapped[dict | None] = mapped_column(Json, nullable=True)
    cost_eur: Mapped[float] = mapped_column(Float, default=0.0)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CorrectionRow(Base):
    __tablename__ = "corrections"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String, ForeignKey("rfqs.message_id"), index=True)
    line: Mapped[int] = mapped_column(Integer)
    field: Mapped[str] = mapped_column(String)
    before: Mapped[str | None] = mapped_column(String, nullable=True)
    after: Mapped[str] = mapped_column(String)
    by: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalCaseRow(Base):
    __tablename__ = "eval_cases"
    case_id: Mapped[str] = mapped_column(String, primary_key=True)
    message_id: Mapped[str] = mapped_column(String, index=True)
    expected: Mapped[dict] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobRow(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String)
    ref: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("jobs_kind_ref", "kind", "ref", unique=True),)


class JournalRow(Base):
    __tablename__ = "journal"
    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    step: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelCallRow(Base):
    __tablename__ = "model_calls"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    route: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    agent: Mapped[str] = mapped_column(String)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_eur: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[float] = mapped_column(Float)
    ok: Mapped[bool] = mapped_column(Boolean)
    fallback: Mapped[bool] = mapped_column(Boolean)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class McpCallRow(Base):
    __tablename__ = "mcp_calls"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tool: Mapped[str] = mapped_column(String, index=True)
    client_id: Mapped[str] = mapped_column(String)
    outcome: Mapped[str] = mapped_column(String)  # ok | denied | error
    latency_ms: Mapped[float] = mapped_column(Float)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LearnedAliasRow(Base):
    __tablename__ = "learned_aliases"
    alias: Mapped[str] = mapped_column(String, primary_key=True)
    grade_id: Mapped[str] = mapped_column(String)
    supporting: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def database_url() -> str:
    return os.environ.get("STEEL_DATABASE_URL", "postgresql+asyncpg://steel@127.0.0.1:5433/steel")


def engine(url: str | None = None) -> AsyncEngine:
    return create_async_engine(url or database_url(), pool_pre_ping=True)


def sessions(eng: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(eng, expire_on_commit=False)
