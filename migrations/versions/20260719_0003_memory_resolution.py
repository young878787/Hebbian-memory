"""add normalized aliases and fail-closed memory resolution staging

Revision ID: 20260719_0003
Revises: 20260719_0002
Create Date: 2026-07-19
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision = "20260719_0003"
down_revision = "20260719_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entity_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("alias_raw", sa.String(255), nullable=False),
        sa.Column("alias_normalized", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_ref", sa.Text()),
        sa.Column("confidence", sa.REAL(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("normalizer_version", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["namespace_id", "entity_id"], ["entities.namespace_id", "entities.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("namespace_id", "entity_type", "alias_normalized", name="uq_entity_aliases_scope_key"),
        sa.CheckConstraint("confidence BETWEEN 0.0 AND 1.0", name="ck_entity_aliases_confidence"),
        sa.CheckConstraint("status IN ('active','pending','rejected')", name="ck_entity_aliases_status"),
    )
    for name in ("topic_raw", "topic_key", "topic_version", "attribute_key", "state_key"):
        size = 64 if name == "state_key" else (32 if name == "topic_version" else 100)
        op.add_column("memories", sa.Column(name, sa.String(size), nullable=True))
    op.create_index("ix_memories_resolution_scope", "memories", ["namespace_id", "memory_type", "topic_key", "status"])
    op.create_table(
        "memory_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ingestion_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("candidate_key", sa.String(64), nullable=False),
        sa.Column("extraction_payload", postgresql.JSONB(), nullable=False),
        sa.Column("normalized_payload", postgresql.JSONB(), nullable=False),
        sa.Column("embedding", VECTOR(2560), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("namespace_id", "candidate_key", name="uq_memory_candidates_scope_key"),
        sa.CheckConstraint("status IN ('pending','resolving','resolved','deferred','failed')", name="ck_memory_candidates_status"),
    )
    op.create_table(
        "memory_resolution_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memory_candidates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("resolver_kind", sa.String(16), nullable=False),
        sa.Column("resolver_model", sa.String(255)),
        sa.Column("resolver_schema_version", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64)),
        sa.Column("candidate_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("target_memory_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("confidence", sa.REAL(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_quotes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("validation_status", sa.String(32), nullable=False),
        sa.Column("before_state", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("after_state", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    raise RuntimeError("memory resolution migration is forward-only; restore from backup to revert")
