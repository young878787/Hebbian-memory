"""create the four scoped Hebbian MVP tables

Revision ID: 20260719_0001
Revises:
Create Date: 2026-07-19
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision = "20260719_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("external_id", sa.String(64), nullable=False, unique=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("memory_type", sa.String(32), nullable=False),
        sa.Column("topic", sa.String(100)),
        sa.Column("occurred_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("importance", sa.REAL(), nullable=False, server_default="0.5"),
        sa.Column("confidence", sa.REAL(), nullable=False, server_default="1.0"),
        sa.Column("source_session_id", sa.String(100)),
        sa.Column("source_message_ids", postgresql.JSONB()),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("embedding", VECTOR(2560), nullable=False),
        sa.CheckConstraint("status IN ('active','superseded','uncertain','archived')", name="ck_memories_status"),
        sa.CheckConstraint("memory_type IN ('character_fact','event','preference','decision')", name="ck_memories_type"),
        sa.CheckConstraint("importance BETWEEN 0.0 AND 1.0", name="ck_memories_importance"),
        sa.CheckConstraint("confidence BETWEEN 0.0 AND 1.0", name="ck_memories_confidence"),
    )
    op.create_index("ix_memories_status_occurred_at", "memories", ["status", sa.text("occurred_at DESC")])
    op.create_table(
        "memory_edges",
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("edge_type", sa.String(32), primary_key=True),
        sa.Column("weight", sa.REAL(), nullable=False, server_default="0.0"),
        sa.Column("activation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_activated_at", sa.DateTime(timezone=True)),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("edge_type IN ('temporal','semantic','co_retrieval','supersedes','contradicts','supports')", name="ck_memory_edges_type"),
        sa.CheckConstraint("weight BETWEEN 0.0 AND 1.0", name="ck_memory_edges_weight"),
        sa.CheckConstraint("activation_count >= 0", name="ck_memory_edges_activation_count"),
        sa.CheckConstraint("source_id <> target_id", name="ck_memory_edges_no_self_loop"),
    )
    op.create_index("ix_memory_edges_target", "memory_edges", ["target_id", "edge_type"])
    op.create_table(
        "retrieval_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("retrieval_mode", sa.String(32), nullable=False),
        sa.Column("run_mode", sa.String(16), nullable=False),
        sa.Column("query_scope", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("total_latency_ms", sa.REAL()),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("retrieval_mode IN ('embedding_only','hebbian')", name="ck_retrieval_runs_mode"),
        sa.CheckConstraint("run_mode IN ('evaluation','learning')", name="ck_retrieval_runs_run_mode"),
        sa.CheckConstraint("query_scope IN ('historical','current','general')", name="ck_retrieval_runs_scope"),
        sa.CheckConstraint("total_latency_ms IS NULL OR total_latency_ms >= 0", name="ck_retrieval_runs_latency"),
    )
    op.create_table(
        "retrieval_items",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("retrieval_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("candidate_rank", sa.Integer(), nullable=False),
        sa.Column("final_rank", sa.Integer()),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("semantic_score", sa.REAL(), nullable=False, server_default="0.0"),
        sa.Column("hebbian_score", sa.REAL(), nullable=False, server_default="0.0"),
        sa.Column("status_adjustment", sa.REAL(), nullable=False, server_default="0.0"),
        sa.Column("final_score", sa.REAL(), nullable=False),
        sa.Column("retrieval_source", sa.String(32), nullable=False),
        sa.Column("activation_path", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.UniqueConstraint("run_id", "candidate_rank"),
        sa.UniqueConstraint("run_id", "final_rank"),
        sa.CheckConstraint("retrieval_source IN ('seed','hebbian')", name="ck_retrieval_items_source"),
        sa.CheckConstraint("semantic_score BETWEEN 0.0 AND 1.0", name="ck_retrieval_items_semantic_score"),
        sa.CheckConstraint("hebbian_score >= 0.0", name="ck_retrieval_items_hebbian_score"),
        sa.CheckConstraint("candidate_rank >= 1 AND (final_rank IS NULL OR final_rank >= 1) AND ((selected = TRUE AND final_rank IS NOT NULL) OR (selected = FALSE AND final_rank IS NULL))", name="ck_retrieval_items_ranks"),
    )
    op.create_index("ix_retrieval_items_run_selected", "retrieval_items", ["run_id", "selected", "final_rank"])


def downgrade() -> None:
    op.drop_index("ix_retrieval_items_run_selected", table_name="retrieval_items")
    op.drop_table("retrieval_items")
    op.drop_table("retrieval_runs")
    op.drop_index("ix_memory_edges_target", table_name="memory_edges")
    op.drop_table("memory_edges")
    op.drop_index("ix_memories_status_occurred_at", table_name="memories")
    op.drop_table("memories")
