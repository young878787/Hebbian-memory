"""add source extraction modality and temporal scope

Revision ID: 20260721_0007
Revises: 20260721_0006
Create Date: 2026-07-21
"""

import sqlalchemy as sa
from alembic import op

revision = "20260721_0007"
down_revision = "20260721_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column("modality", sa.String(16), nullable=False, server_default="asserted"),
    )
    op.add_column(
        "memories",
        sa.Column("temporal_scope", sa.String(16), nullable=False, server_default="unknown"),
    )
    op.add_column(
        "memory_claims",
        sa.Column("temporal_scope", sa.String(16), nullable=False, server_default="unknown"),
    )
    op.create_check_constraint(
        "ck_memories_modality",
        "memories",
        "modality IN ('asserted','question','uncertain','considered')",
    )
    op.create_check_constraint(
        "ck_memories_temporal_scope",
        "memories",
        "temporal_scope IN ('current','historical','unknown')",
    )
    op.create_check_constraint(
        "ck_claims_temporal_scope",
        "memory_claims",
        "temporal_scope IN ('current','historical','unknown')",
    )
    op.create_index(
        "ix_memories_namespace_temporal_status_occurred_at",
        "memories",
        ["namespace_id", "temporal_scope", "status", "occurred_at"],
    )


def downgrade() -> None:
    raise RuntimeError("extraction semantics migration is forward-only; restore from backup to revert")
