"""add canonical claim, association, lifecycle, and graph projection ledgers

Revision ID: 20260720_0005
Revises: 20260720_0004
Create Date: 2026-07-20

The migration is deliberately additive.  Existing ``memories`` and
``memory_edges`` remain readable while applications dual-write the new ledger.
Backfill is report-only at this revision; no historical source is fabricated.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260720_0005"
down_revision = "20260720_0004"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
TIMESTAMP = sa.DateTime(timezone=True)


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMP, nullable=False, server_default=sa.text("now()")),
    ]


def upgrade() -> None:
    op.create_table(
        "memory_claims",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("namespace_id", UUID, nullable=False),
        sa.Column("legacy_memory_id", UUID, nullable=False),
        sa.Column("claim_type", sa.String(32), nullable=False),
        sa.Column("subject_entity_id", UUID),
        sa.Column("predicate_key", sa.String(100)),
        sa.Column("object_value", sa.Text(), nullable=False),
        sa.Column("object_entity_id", UUID),
        sa.Column("event_time", TIMESTAMP),
        sa.Column("valid_from", TIMESTAMP),
        sa.Column("valid_to", TIMESTAMP),
        sa.Column("polarity", sa.String(16), nullable=False, server_default="affirmed"),
        sa.Column("modality", sa.String(16), nullable=False, server_default="asserted"),
        sa.Column("confidence", sa.REAL(), nullable=False),
        sa.Column("importance", sa.REAL(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("state_key", sa.String(64)),
        sa.Column("schema_version", sa.String(64), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["namespace_id"], ["memory_namespaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["namespace_id", "legacy_memory_id"], ["memories.namespace_id", "memories.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("namespace_id", "id", name="uq_claims_namespace_id"),
        sa.UniqueConstraint("namespace_id", "legacy_memory_id", name="uq_claims_namespace_memory"),
        sa.CheckConstraint("status IN ('active','superseded','uncertain','archived')", name="ck_claims_status"),
        sa.CheckConstraint("confidence BETWEEN 0.0 AND 1.0", name="ck_claims_confidence"),
        sa.CheckConstraint("importance BETWEEN 0.0 AND 1.0", name="ck_claims_importance"),
    )
    op.create_index("uq_claims_one_active_state", "memory_claims", ["namespace_id", "state_key"], unique=True, postgresql_where=sa.text("status = 'active' AND state_key IS NOT NULL"))
    op.create_table(
        "claim_evidence",
        sa.Column("id", UUID, primary_key=True), sa.Column("namespace_id", UUID, nullable=False),
        sa.Column("claim_id", UUID, nullable=False), sa.Column("source_message_id", sa.String(128), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=False), sa.Column("evidence_start", sa.Integer()), sa.Column("evidence_end", sa.Integer()),
        sa.Column("extractor_model", sa.String(255), nullable=False), sa.Column("extraction_schema_version", sa.String(64), nullable=False), *_audit_columns(),
        sa.ForeignKeyConstraint(["namespace_id", "claim_id"], ["memory_claims.namespace_id", "memory_claims.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["namespace_id", "source_message_id"], ["source_messages.namespace_id", "source_messages.message_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("claim_id", "source_message_id", "evidence_text", name="uq_claim_evidence_source_text"),
    )
    op.create_table(
        "relation_evidence",
        sa.Column("id", UUID, primary_key=True), sa.Column("namespace_id", UUID, nullable=False),
        sa.Column("source_claim_id", UUID, nullable=False), sa.Column("target_claim_id", UUID, nullable=False),
        sa.Column("relation_type", sa.String(16), nullable=False), sa.Column("direction", sa.String(16), nullable=False, server_default="directed"),
        sa.Column("origin", sa.String(32), nullable=False), sa.Column("evidence_refs", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("confidence", sa.REAL(), nullable=False), sa.Column("valid_from", TIMESTAMP), sa.Column("valid_to", TIMESTAMP),
        sa.Column("resolution_status", sa.String(16), nullable=False, server_default="active"), *_audit_columns(),
        sa.ForeignKeyConstraint(["namespace_id"], ["memory_namespaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["namespace_id", "source_claim_id"], ["memory_claims.namespace_id", "memory_claims.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["namespace_id", "target_claim_id"], ["memory_claims.namespace_id", "memory_claims.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("relation_type IN ('supports','contradicts','supersedes','temporal')", name="ck_relation_evidence_type"),
        sa.CheckConstraint("source_claim_id <> target_claim_id", name="ck_relation_evidence_no_self"),
    )
    op.create_table(
        "association_events",
        sa.Column("id", UUID, primary_key=True), sa.Column("namespace_id", UUID, nullable=False),
        sa.Column("source_memory_id", UUID, nullable=False), sa.Column("target_memory_id", UUID, nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False), sa.Column("delta", sa.REAL(), nullable=False),
        sa.Column("retrieval_run_id", UUID), sa.Column("evidence_refs", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("occurred_at", TIMESTAMP, nullable=False), sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False), *_audit_columns(),
        sa.ForeignKeyConstraint(["namespace_id"], ["memory_namespaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["namespace_id", "source_memory_id"], ["memories.namespace_id", "memories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["namespace_id", "target_memory_id"], ["memories.namespace_id", "memories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["retrieval_run_id"], ["retrieval_runs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("idempotency_key", name="uq_association_events_idempotency"),
        sa.CheckConstraint("event_type IN ('cited_together','explicit_positive_feedback','explicit_negative_feedback','answer_rejected','manual_link','decay_checkpoint')", name="ck_association_events_type"),
        sa.CheckConstraint("source_memory_id < target_memory_id", name="ck_association_events_order"),
    )
    op.create_table(
        "association_stats",
        sa.Column("namespace_id", UUID, primary_key=True), sa.Column("source_memory_id", UUID, primary_key=True), sa.Column("target_memory_id", UUID, primary_key=True),
        sa.Column("positive_strength", sa.REAL(), nullable=False, server_default="0"), sa.Column("negative_strength", sa.REAL(), nullable=False, server_default="0"),
        sa.Column("activation_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("rejection_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_activated_at", TIMESTAMP), sa.Column("last_reinforced_at", TIMESTAMP), sa.Column("effective_weight", sa.REAL(), nullable=False, server_default="0"), sa.Column("decay_policy_version", sa.String(64), nullable=False), *_audit_columns(),
        sa.ForeignKeyConstraint(["namespace_id", "source_memory_id"], ["memories.namespace_id", "memories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["namespace_id", "target_memory_id"], ["memories.namespace_id", "memories.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("source_memory_id < target_memory_id", name="ck_association_stats_order"),
    )
    op.create_table(
        "lifecycle_decisions", sa.Column("id", UUID, primary_key=True), sa.Column("namespace_id", UUID, nullable=False), sa.Column("claim_id", UUID, nullable=False),
        sa.Column("action", sa.String(32), nullable=False), sa.Column("policy_version", sa.String(64), nullable=False), sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("before_state", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")), sa.Column("after_state", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")), sa.Column("rollback_payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")), *_audit_columns(),
        sa.ForeignKeyConstraint(["namespace_id"], ["memory_namespaces.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["namespace_id", "claim_id"], ["memory_claims.namespace_id", "memory_claims.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "graph_projection_runs", sa.Column("id", UUID, primary_key=True), sa.Column("namespace_id", UUID, nullable=False), sa.Column("projection_version", sa.String(64), nullable=False), sa.Column("source_snapshot_hash", sa.String(64), nullable=False), sa.Column("config_snapshot", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")), sa.Column("included_claim_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("excluded_claim_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("node_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("edge_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("blocker_counts", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")), sa.Column("status", sa.String(16), nullable=False), sa.Column("started_at", TIMESTAMP, nullable=False, server_default=sa.text("now()")), sa.Column("completed_at", TIMESTAMP), *_audit_columns(),
        sa.ForeignKeyConstraint(["namespace_id"], ["memory_namespaces.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "graph_projection_nodes", sa.Column("id", UUID, primary_key=True), sa.Column("projection_run_id", UUID, nullable=False), sa.Column("node_key", sa.String(255), nullable=False), sa.Column("node_type", sa.String(32), nullable=False), sa.Column("claim_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")), *_audit_columns(),
        sa.ForeignKeyConstraint(["projection_run_id"], ["graph_projection_runs.id"], ondelete="CASCADE"), sa.CheckConstraint("jsonb_array_length(claim_ids) > 0", name="ck_projection_node_provenance"),
    )
    op.create_table(
        "graph_projection_edges", sa.Column("id", UUID, primary_key=True), sa.Column("projection_run_id", UUID, nullable=False), sa.Column("source_key", sa.String(255), nullable=False), sa.Column("target_key", sa.String(255), nullable=False), sa.Column("edge_type", sa.String(32), nullable=False), sa.Column("claim_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")), sa.Column("relation_evidence_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")), *_audit_columns(),
        sa.ForeignKeyConstraint(["projection_run_id"], ["graph_projection_runs.id"], ondelete="CASCADE"), sa.CheckConstraint("jsonb_array_length(claim_ids) > 0 OR jsonb_array_length(relation_evidence_ids) > 0", name="ck_projection_edge_provenance"),
    )
    for table in (
        "memory_claims", "claim_evidence", "relation_evidence", "association_events",
        "association_stats", "lifecycle_decisions", "graph_projection_runs", "graph_projection_nodes", "graph_projection_edges",
    ):
        op.execute(sa.text(f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION hela_mem_zh_mvp_set_updated_at()"))


def downgrade() -> None:
    raise RuntimeError("canonical-memory migration is forward-only; restore from backup to revert")
