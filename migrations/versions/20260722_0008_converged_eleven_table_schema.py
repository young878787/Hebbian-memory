"""Converge persistence to eleven responsibility-owned tables.

Revision ID: 20260722_0008
Revises: 20260721_0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260722_0008"
down_revision = "20260721_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())

    # Run- and candidate-local latest state replaces two history tables.
    op.add_column(
        "ingestion_runs",
        sa.Column("extraction_outcomes", jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.execute(
        sa.text(
            """
            UPDATE ingestion_runs run
            SET extraction_outcomes = source.outcomes
            FROM (
              SELECT ingestion_run_id,
                     jsonb_agg(
                       jsonb_build_object(
                         'source_message_id', source_message_id,
                         'schema_version', schema_version,
                         'status', status,
                         'reason_code', reason_code,
                         'provider_attempts', provider_attempts,
                         'error', error
                       ) ORDER BY created_at, id
                     ) AS outcomes
              FROM message_extraction_outcomes
              GROUP BY ingestion_run_id
            ) source
            WHERE run.id = source.ingestion_run_id
            """
        )
    )
    op.add_column(
        "memory_candidates",
        sa.Column("latest_decision", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.execute(
        sa.text(
            """
            UPDATE memory_candidates candidate
            SET latest_decision = source.payload
            FROM (
              SELECT DISTINCT ON (candidate_id)
                     candidate_id,
                     to_jsonb(memory_resolution_decisions)
                       - 'id' - 'namespace_id' - 'candidate_id' - 'created_at' - 'updated_at'
                       AS payload
              FROM memory_resolution_decisions
              ORDER BY candidate_id, created_at DESC, id DESC
            ) source
            WHERE candidate.id = source.candidate_id
            """
        )
    )

    # Evidence points directly to the canonical memory instead of a 1:1 claim copy.
    op.add_column("claim_evidence", sa.Column("memory_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE claim_evidence evidence
            SET memory_id = claim.legacy_memory_id
            FROM memory_claims claim
            WHERE evidence.namespace_id = claim.namespace_id
              AND evidence.claim_id = claim.id
            """
        )
    )
    op.drop_constraint(
        "claim_evidence_namespace_id_claim_id_fkey", "claim_evidence", type_="foreignkey"
    )
    op.drop_constraint("uq_claim_evidence_source_text", "claim_evidence", type_="unique")
    op.drop_column("claim_evidence", "claim_id")
    op.rename_table("claim_evidence", "memory_evidence")
    op.alter_column("memory_evidence", "memory_id", nullable=False)
    op.create_foreign_key(
        "fk_memory_evidence_memory",
        "memory_evidence",
        "memories",
        ["namespace_id", "memory_id"],
        ["namespace_id", "id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_memory_evidence_source_text",
        "memory_evidence",
        ["memory_id", "source_message_id", "evidence_text"],
    )

    # Preserve legacy non-normalized provenance without keeping active duplicate columns.
    op.execute(
        sa.text(
            """
            UPDATE memories
            SET metadata = jsonb_set(
                COALESCE(metadata, '{}'::jsonb),
                '{legacy_source_message_ids}',
                source_message_ids,
                true
            )
            WHERE source_message_ids IS NOT NULL
              AND jsonb_array_length(source_message_ids) > 0
            """
        )
    )
    op.drop_column("memories", "source_message_ids")
    op.execute(
        sa.text(
            """
            UPDATE entities
            SET metadata = jsonb_set(
                COALESCE(metadata, '{}'::jsonb),
                '{legacy_aliases}',
                aliases,
                true
            )
            WHERE aliases IS NOT NULL AND jsonb_array_length(aliases) > 0
            """
        )
    )
    op.drop_column("entities", "aliases")

    # One relation table owns truth, direction, provenance, and retrieval weight.
    op.alter_column("memory_edges", "edge_type", new_column_name="relation_type")
    op.add_column("memory_edges", sa.Column("status", sa.String(16), nullable=False, server_default="active"))
    op.add_column("memory_edges", sa.Column("origin", sa.String(32), nullable=True))
    op.add_column("memory_edges", sa.Column("confidence", sa.REAL(), nullable=False, server_default="1.0"))
    op.add_column("memory_edges", sa.Column("evidence_refs", jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.execute(
        sa.text(
            """
            UPDATE memory_edges
            SET origin = COALESCE(metadata->>'origin', 'legacy'),
                evidence_refs = CASE
                  WHEN metadata ? 'evidence' THEN jsonb_build_array(metadata->>'evidence')
                  ELSE '[]'::jsonb
                END
            """
        )
    )
    op.alter_column("memory_edges", "origin", nullable=False)
    op.execute(
        sa.text(
            """
            INSERT INTO memory_edges (
              namespace_id, source_id, target_id, relation_type, weight,
              activation_count, metadata, created_at, updated_at,
              status, origin, confidence, evidence_refs
            )
            SELECT relation.namespace_id,
                   source_claim.legacy_memory_id,
                   target_claim.legacy_memory_id,
                   relation.relation_type,
                   relation.confidence,
                   0,
                   jsonb_build_object('migrated_from', 'relation_evidence'),
                   relation.created_at,
                   relation.updated_at,
                   relation.resolution_status,
                   relation.origin,
                   relation.confidence,
                   relation.evidence_refs
            FROM relation_evidence relation
            JOIN memory_claims source_claim
              ON source_claim.namespace_id = relation.namespace_id
             AND source_claim.id = relation.source_claim_id
            JOIN memory_claims target_claim
              ON target_claim.namespace_id = relation.namespace_id
             AND target_claim.id = relation.target_claim_id
            ON CONFLICT (namespace_id, source_id, target_id, relation_type)
            DO UPDATE SET
              weight = GREATEST(memory_edges.weight, EXCLUDED.weight),
              confidence = GREATEST(memory_edges.confidence, EXCLUDED.confidence),
              evidence_refs = memory_edges.evidence_refs || EXCLUDED.evidence_refs,
              updated_at = GREATEST(memory_edges.updated_at, EXCLUDED.updated_at)
            """
        )
    )
    # Symmetric relations are stored once; retrieval expands both directions in memory.
    op.execute(
        sa.text(
            """
            UPDATE memory_edges low
            SET weight = GREATEST(low.weight, high.weight),
                confidence = GREATEST(low.confidence, high.confidence),
                evidence_refs = low.evidence_refs || high.evidence_refs
            FROM memory_edges high
            WHERE low.namespace_id = high.namespace_id
              AND low.source_id = high.target_id
              AND low.target_id = high.source_id
              AND low.relation_type = high.relation_type
              AND low.relation_type IN ('semantic', 'contradicts')
              AND low.source_id < low.target_id
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM memory_edges
            WHERE relation_type IN ('semantic', 'contradicts')
              AND source_id > target_id
            """
        )
    )
    op.drop_constraint("ck_memory_edges_type", "memory_edges", type_="check")
    op.create_check_constraint(
        "ck_memory_relations_type",
        "memory_edges",
        "relation_type IN ('temporal','semantic','supersedes','contradicts','supports')",
    )
    op.create_check_constraint(
        "ck_memory_relations_confidence", "memory_edges", "confidence BETWEEN 0.0 AND 1.0"
    )
    op.drop_index("ix_memory_edges_namespace_target", table_name="memory_edges")
    op.rename_table("memory_edges", "memory_relations")
    op.create_index(
        "ix_memory_relations_namespace_target",
        "memory_relations",
        ["namespace_id", "target_id", "relation_type"],
    )

    # Materialize any event-only pairs before discarding event history.
    op.execute(
        sa.text(
            """
            INSERT INTO association_stats (
              namespace_id, source_memory_id, target_memory_id,
              positive_strength, negative_strength, activation_count,
              success_count, rejection_count, last_activated_at,
              last_reinforced_at, effective_weight, decay_policy_version,
              created_at, updated_at
            )
            SELECT namespace_id, source_memory_id, target_memory_id,
                   SUM(CASE WHEN event_type IN ('cited_together','explicit_positive_feedback','manual_link') THEN delta ELSE 0 END),
                   SUM(CASE WHEN event_type IN ('explicit_negative_feedback','answer_rejected') THEN abs(delta) ELSE 0 END),
                   count(*),
                   SUM(CASE WHEN event_type IN ('cited_together','explicit_positive_feedback','manual_link') THEN 1 ELSE 0 END),
                   SUM(CASE WHEN event_type IN ('explicit_negative_feedback','answer_rejected') THEN 1 ELSE 0 END),
                   max(occurred_at), max(occurred_at),
                   LEAST(1.0, GREATEST(0.0,
                     SUM(CASE WHEN event_type IN ('cited_together','explicit_positive_feedback','manual_link') THEN delta ELSE 0 END)
                     - SUM(CASE WHEN event_type IN ('explicit_negative_feedback','answer_rejected') THEN abs(delta) ELSE 0 END)
                   )),
                   'association-v2', min(created_at), max(updated_at)
            FROM association_events
            GROUP BY namespace_id, source_memory_id, target_memory_id
            ON CONFLICT (namespace_id, source_memory_id, target_memory_id)
            DO UPDATE SET
              positive_strength = EXCLUDED.positive_strength,
              negative_strength = EXCLUDED.negative_strength,
              activation_count = EXCLUDED.activation_count,
              success_count = EXCLUDED.success_count,
              rejection_count = EXCLUDED.rejection_count,
              last_activated_at = EXCLUDED.last_activated_at,
              last_reinforced_at = EXCLUDED.last_reinforced_at,
              effective_weight = EXCLUDED.effective_weight,
              decay_policy_version = EXCLUDED.decay_policy_version,
              updated_at = EXCLUDED.updated_at
            """
        )
    )
    op.drop_constraint("ck_association_stats_order", "association_stats", type_="check")
    op.alter_column("association_stats", "decay_policy_version", new_column_name="policy_version")
    op.add_column("association_stats", sa.Column("last_learning_token", sa.String(128), nullable=True))
    op.add_column("association_stats", sa.Column("evidence_refs", jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.drop_column("association_stats", "last_reinforced_at")
    op.rename_table("association_stats", "memory_associations")
    op.create_check_constraint(
        "ck_memory_associations_order",
        "memory_associations",
        "source_memory_id < target_memory_id",
    )

    # Contract phase: remove historical/rebuildable tables in FK-safe order.
    op.drop_table("graph_projection_edges")
    op.drop_table("graph_projection_nodes")
    op.drop_table("graph_projection_runs")
    op.drop_table("lifecycle_decisions")
    op.drop_table("relation_evidence")
    op.drop_table("association_events")
    op.drop_table("retrieval_items")
    op.drop_table("retrieval_runs")
    op.drop_table("message_extraction_outcomes")
    op.drop_table("memory_resolution_decisions")
    op.drop_table("memory_claims")


def downgrade() -> None:
    raise RuntimeError(
        "eleven-table convergence is forward-only; restore results/schema_convergence_backup"
    )
