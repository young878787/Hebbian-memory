"""add namespace-scoped pipeline tables and constraints

Revision ID: 20260719_0002
Revises: 20260719_0001
Create Date: 2026-07-19
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260719_0002"
down_revision = "20260719_0001"
branch_labels = None
depends_on = None

LEGACY_NAMESPACE_KEY = "legacy-mvp-v0.4"


def upgrade() -> None:
    op.create_table(
        "memory_namespaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace_key", sa.String(128), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    legacy_id = uuid.uuid4()
    # Use literal SQL instead of bulk_insert so `alembic upgrade --sql` can
    # render the JSONB seed row as well as an online PostgreSQL migration.
    op.execute(
        "INSERT INTO memory_namespaces (id, namespace_key, display_name, metadata) "
        f"VALUES ('{legacy_id}', '{LEGACY_NAMESPACE_KEY}', 'v0.4 migrated records', "
        f"'{{\"migration\": \"{revision}\"}}'::jsonb)"
    )
    op.add_column("memories", sa.Column("namespace_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("memories", sa.Column("canonical_key", sa.String(128), nullable=True))
    op.add_column("memories", sa.Column("extraction_schema_version", sa.String(64), nullable=True))
    op.get_bind().execute(
        sa.text(
            "UPDATE memories SET namespace_id = :namespace_id, canonical_key = external_id, "
            "extraction_schema_version = 'fixture-v0.4'"
        ),
        {"namespace_id": legacy_id},
    )
    op.alter_column("memories", "namespace_id", nullable=False)
    op.alter_column("memories", "canonical_key", nullable=False)
    op.alter_column("memories", "extraction_schema_version", nullable=False)
    op.create_foreign_key("fk_memories_namespace", "memories", "memory_namespaces", ["namespace_id"], ["id"], ondelete="RESTRICT")
    op.drop_constraint("memories_external_id_key", "memories", type_="unique")
    op.create_unique_constraint("uq_memories_namespace_external_id", "memories", ["namespace_id", "external_id"])
    op.create_unique_constraint("uq_memories_namespace_id", "memories", ["namespace_id", "id"])
    op.create_index("ix_memories_namespace_status_occurred_at", "memories", ["namespace_id", "status", sa.text("occurred_at DESC")])

    op.create_table(
        "source_messages",
        sa.Column("namespace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("message_id", sa.String(128), primary_key=True),
        sa.Column("session_id", sa.String(100), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("role IN ('user','assistant')", name="ck_source_messages_role"),
    )
    op.create_table(
        "ingestion_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("extractor_model", sa.String(255), nullable=False),
        sa.Column("extractor_schema_version", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        *[sa.Column(name, sa.Integer(), nullable=False, server_default="0") for name in ("message_count", "created_count", "merged_count", "superseded_count", "contradicted_count", "ignored_count", "error_count")],
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_table(
        "entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memory_namespaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("canonical_name", sa.String(255), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("aliases", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("confidence", sa.REAL(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("namespace_id", "entity_type", "canonical_name", name="uq_entities_namespace_type_name"),
        sa.UniqueConstraint("namespace_id", "id", name="uq_entities_namespace_id"),
    )
    op.create_table(
        "memory_entities",
        sa.Column("namespace_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("mention_role", sa.String(32)),
        sa.Column("confidence", sa.REAL(), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id", "memory_id"], ["memories.namespace_id", "memories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["namespace_id", "entity_id"], ["entities.namespace_id", "entities.id"], ondelete="CASCADE"),
    )
    op.add_column("memory_edges", sa.Column("namespace_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.get_bind().execute(
        sa.text("UPDATE memory_edges SET namespace_id = :namespace_id"),
        {"namespace_id": legacy_id},
    )
    op.alter_column("memory_edges", "namespace_id", nullable=False)
    op.drop_constraint("memory_edges_pkey", "memory_edges", type_="primary")
    op.create_primary_key("memory_edges_pkey", "memory_edges", ["namespace_id", "source_id", "target_id", "edge_type"])
    op.drop_constraint("memory_edges_source_id_fkey", "memory_edges", type_="foreignkey")
    op.drop_constraint("memory_edges_target_id_fkey", "memory_edges", type_="foreignkey")
    op.create_foreign_key("fk_memory_edges_source", "memory_edges", "memories", ["namespace_id", "source_id"], ["namespace_id", "id"], ondelete="CASCADE")
    op.create_foreign_key("fk_memory_edges_target", "memory_edges", "memories", ["namespace_id", "target_id"], ["namespace_id", "id"], ondelete="CASCADE")
    op.drop_index("ix_memory_edges_target", table_name="memory_edges")
    op.create_index("ix_memory_edges_namespace_target", "memory_edges", ["namespace_id", "target_id", "edge_type"])

    op.add_column("retrieval_runs", sa.Column("namespace_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.get_bind().execute(
        sa.text("UPDATE retrieval_runs SET namespace_id = :namespace_id"),
        {"namespace_id": legacy_id},
    )
    op.alter_column("retrieval_runs", "namespace_id", nullable=False)
    op.create_foreign_key("fk_retrieval_runs_namespace", "retrieval_runs", "memory_namespaces", ["namespace_id"], ["id"], ondelete="RESTRICT")
    op.create_unique_constraint("uq_retrieval_runs_namespace_id", "retrieval_runs", ["namespace_id", "id"])
    op.add_column("retrieval_items", sa.Column("namespace_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute(sa.text("UPDATE retrieval_items i SET namespace_id = r.namespace_id FROM retrieval_runs r WHERE i.run_id = r.id"))
    op.alter_column("retrieval_items", "namespace_id", nullable=False)
    op.drop_constraint("retrieval_items_run_id_fkey", "retrieval_items", type_="foreignkey")
    op.drop_constraint("retrieval_items_memory_id_fkey", "retrieval_items", type_="foreignkey")
    op.create_foreign_key("fk_retrieval_items_run", "retrieval_items", "retrieval_runs", ["namespace_id", "run_id"], ["namespace_id", "id"], ondelete="CASCADE")
    op.create_foreign_key("fk_retrieval_items_memory", "retrieval_items", "memories", ["namespace_id", "memory_id"], ["namespace_id", "id"], ondelete="CASCADE")


def downgrade() -> None:
    raise RuntimeError("v0.5 namespace migration is intentionally forward-only; restore from backup to revert")
