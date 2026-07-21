"""add per-message extraction outcomes

Revision ID: 20260721_0006
Revises: 20260720_0005
Create Date: 2026-07-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260721_0006"
down_revision = "20260720_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_extraction_outcomes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_message_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64)),
        sa.Column("provider_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["namespace_id"], ["memory_namespaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["namespace_id", "source_message_id"], ["source_messages.namespace_id", "source_messages.message_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("ingestion_run_id", "source_message_id", name="uq_message_outcomes_run_message"),
        sa.CheckConstraint("status IN ('EXTRACTED','NO_MEMORY','FAILED')", name="ck_message_outcomes_status"),
    )
    op.execute(
        "CREATE TRIGGER trg_message_extraction_outcomes_updated_at "
        "BEFORE UPDATE ON message_extraction_outcomes FOR EACH ROW "
        "EXECUTE FUNCTION hela_mem_zh_mvp_set_updated_at()"
    )


def downgrade() -> None:
    raise RuntimeError("per-message extraction audit migration is forward-only; restore from backup to revert")
