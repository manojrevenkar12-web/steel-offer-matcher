"""initial schema

Revision ID: 0001
Revises: 
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import pgvector.sqlalchemy

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table('eval_cases',
    sa.Column('case_id', sa.String(), nullable=False),
    sa.Column('message_id', sa.String(), nullable=False),
    sa.Column('expected', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('case_id')
    )
    op.create_index(op.f('ix_eval_cases_message_id'), 'eval_cases', ['message_id'], unique=False)
    op.create_table('jobs',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('ref', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_jobs_status'), 'jobs', ['status'], unique=False)
    op.create_index('jobs_kind_ref', 'jobs', ['kind', 'ref'], unique=True)
    op.create_table('journal',
    sa.Column('run_id', sa.String(), nullable=False),
    sa.Column('step', sa.String(), nullable=False),
    sa.Column('value', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('run_id', 'step')
    )
    op.create_table('layouts',
    sa.Column('fingerprint', sa.String(), nullable=False),
    sa.Column('mapping', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('approved_by', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('fingerprint')
    )
    op.create_table('learned_aliases',
    sa.Column('alias', sa.String(), nullable=False),
    sa.Column('grade_id', sa.String(), nullable=False),
    sa.Column('supporting', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('alias')
    )
    op.create_table('mcp_calls',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('tool', sa.String(), nullable=False),
    sa.Column('client_id', sa.String(), nullable=False),
    sa.Column('outcome', sa.String(), nullable=False),
    sa.Column('latency_ms', sa.Float(), nullable=False),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_mcp_calls_tool'), 'mcp_calls', ['tool'], unique=False)
    op.create_table('model_calls',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('route', sa.String(), nullable=False),
    sa.Column('model', sa.String(), nullable=False),
    sa.Column('agent', sa.String(), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cost_eur', sa.Float(), nullable=False),
    sa.Column('latency_ms', sa.Float(), nullable=False),
    sa.Column('ok', sa.Boolean(), nullable=False),
    sa.Column('fallback', sa.Boolean(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('rfqs',
    sa.Column('message_id', sa.String(), nullable=False),
    sa.Column('received', sa.String(), nullable=False),
    sa.Column('sender', sa.String(), nullable=False),
    sa.Column('subject', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('raw', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('result', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('cost_eur', sa.Float(), nullable=False),
    sa.Column('model_calls', sa.Integer(), nullable=False),
    sa.Column('trace_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('message_id')
    )
    op.create_index(op.f('ix_rfqs_status'), 'rfqs', ['status'], unique=False)
    op.create_table('stock_lists',
    sa.Column('list_id', sa.String(), nullable=False),
    sa.Column('supplier_id', sa.String(), nullable=False),
    sa.Column('filename', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('problems', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('items', sa.Integer(), nullable=False),
    sa.Column('skipped', sa.Integer(), nullable=False),
    sa.Column('used_model', sa.Boolean(), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('list_id')
    )
    op.create_index(op.f('ix_stock_lists_supplier_id'), 'stock_lists', ['supplier_id'], unique=False)
    op.create_table('corrections',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('message_id', sa.String(), nullable=False),
    sa.Column('line', sa.Integer(), nullable=False),
    sa.Column('field', sa.String(), nullable=False),
    sa.Column('before', sa.String(), nullable=True),
    sa.Column('after', sa.String(), nullable=False),
    sa.Column('by', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['message_id'], ['rfqs.message_id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_corrections_message_id'), 'corrections', ['message_id'], unique=False)
    op.create_table('stock_items',
    sa.Column('item_id', sa.String(), nullable=False),
    sa.Column('list_id', sa.String(), nullable=False),
    sa.Column('supplier_id', sa.String(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('form', sa.String(), nullable=False),
    sa.Column('finish', sa.String(), nullable=False),
    sa.Column('grade_id', sa.String(), nullable=True),
    sa.Column('grade_text', sa.String(), nullable=False),
    sa.Column('coating', sa.String(), nullable=True),
    sa.Column('surface', sa.String(), nullable=True),
    sa.Column('thickness_mm', sa.Float(), nullable=False),
    sa.Column('width_mm', sa.Float(), nullable=False),
    sa.Column('length_mm', sa.Float(), nullable=True),
    sa.Column('weight_t', sa.Float(), nullable=False),
    sa.Column('price_eur_t', sa.Float(), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('origin', sa.String(length=2), nullable=True),
    sa.Column('available_from', sa.Date(), nullable=True),
    sa.Column('certificate', sa.String(length=4), nullable=False),
    sa.Column('quality', sa.String(), nullable=False),
    sa.Column('edge', sa.String(), nullable=False),
    sa.Column('search_text', sa.Text(), nullable=False),
    sa.Column('tsv', postgresql.TSVECTOR(), sa.Computed("to_tsvector('simple', search_text)", persisted=True), nullable=True),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=256), nullable=True),
    sa.ForeignKeyConstraint(['list_id'], ['stock_lists.list_id'], ),
    sa.PrimaryKeyConstraint('item_id')
    )
    op.create_index(op.f('ix_stock_items_active'), 'stock_items', ['active'], unique=False)
    op.create_index(op.f('ix_stock_items_grade_id'), 'stock_items', ['grade_id'], unique=False)
    op.create_index(op.f('ix_stock_items_list_id'), 'stock_items', ['list_id'], unique=False)
    op.create_index(op.f('ix_stock_items_supplier_id'), 'stock_items', ['supplier_id'], unique=False)
    op.create_index("stock_items_tsv", "stock_items", ["tsv"], postgresql_using="gin")
    op.execute("CREATE INDEX stock_items_embedding ON stock_items USING hnsw (embedding vector_cosine_ops)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS stock_items_embedding")
    op.drop_index("stock_items_tsv", table_name="stock_items")
    op.drop_index(op.f('ix_stock_items_supplier_id'), table_name='stock_items')
    op.drop_index(op.f('ix_stock_items_list_id'), table_name='stock_items')
    op.drop_index(op.f('ix_stock_items_grade_id'), table_name='stock_items')
    op.drop_index(op.f('ix_stock_items_active'), table_name='stock_items')
    op.drop_table('stock_items')
    op.drop_index(op.f('ix_corrections_message_id'), table_name='corrections')
    op.drop_table('corrections')
    op.drop_index(op.f('ix_stock_lists_supplier_id'), table_name='stock_lists')
    op.drop_table('stock_lists')
    op.drop_index(op.f('ix_rfqs_status'), table_name='rfqs')
    op.drop_table('rfqs')
    op.drop_table('model_calls')
    op.drop_index(op.f('ix_mcp_calls_tool'), table_name='mcp_calls')
    op.drop_table('mcp_calls')
    op.drop_table('learned_aliases')
    op.drop_table('layouts')
    op.drop_table('journal')
    op.drop_index('jobs_kind_ref', table_name='jobs')
    op.drop_index(op.f('ix_jobs_status'), table_name='jobs')
    op.drop_table('jobs')
    op.drop_index(op.f('ix_eval_cases_message_id'), table_name='eval_cases')
    op.drop_table('eval_cases')
