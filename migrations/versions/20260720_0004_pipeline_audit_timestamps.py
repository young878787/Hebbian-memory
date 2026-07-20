"""add created_at and updated_at to all persisted pipeline tables

Revision ID: 20260720_0004
Revises: 20260719_0003
Create Date: 2026-07-20
"""

import sqlalchemy as sa
from alembic import op

revision = "20260720_0004"
down_revision = "20260719_0003"
branch_labels = None
depends_on = None

PIPELINE_TABLES = (
    "memory_namespaces",
    "source_messages",
    "ingestion_runs",
    "entities",
    "entity_aliases",
    "memories",
    "memory_entities",
    "memory_candidates",
    "memory_resolution_decisions",
    "memory_edges",
    "retrieval_runs",
    "retrieval_items",
)

TABLES_WITH_CREATED_AT = {
    "memory_namespaces",
    "entity_aliases",
    "memories",
    "memory_candidates",
    "memory_resolution_decisions",
    "retrieval_runs",
}

TABLES_WITH_UPDATED_AT = {"memory_candidates"}


def upgrade() -> None:
    timestamp = sa.DateTime(timezone=True)
    for table_name in PIPELINE_TABLES:
        if table_name not in TABLES_WITH_CREATED_AT:
            op.add_column(
                table_name,
                sa.Column(
                    "created_at",
                    timestamp,
                    nullable=False,
                    server_default=sa.text("now()"),
                ),
            )
        if table_name not in TABLES_WITH_UPDATED_AT:
            op.add_column(
                table_name,
                sa.Column(
                    "updated_at",
                    timestamp,
                    nullable=False,
                    server_default=sa.text("now()"),
                ),
            )

    op.execute(
        """
        CREATE FUNCTION hela_mem_zh_mvp_set_updated_at()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$;
        """
    )
    for table_name in PIPELINE_TABLES:
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table_name}_updated_at "
                f"BEFORE UPDATE ON {table_name} "
                "FOR EACH ROW EXECUTE FUNCTION hela_mem_zh_mvp_set_updated_at()"
            )
        )


def downgrade() -> None:
    raise RuntimeError("pipeline audit timestamp migration is forward-only; restore from backup to revert")
