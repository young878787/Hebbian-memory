"""Database creation and target guards."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .settings import EXPECTED_DATABASE, Settings

CANONICAL_ARCHITECTURE_REVISION = "20260721_0006"
CANONICAL_ARCHITECTURE_TABLES = frozenset(
    {
        "memory_claims",
        "claim_evidence",
        "relation_evidence",
        "association_events",
        "association_stats",
        "lifecycle_decisions",
        "graph_projection_runs",
        "graph_projection_nodes",
        "graph_projection_edges",
        "message_extraction_outcomes",
    }
)


class SchemaCompatibilityError(ValueError):
    """The application revision requires tables absent from the target database."""


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10},
    )


def verify_database_target(engine: Engine, settings: Settings) -> None:
    with engine.connect() as connection:
        database, user = connection.execute(text("SELECT current_database(), current_user")).one()
        has_vector = connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
        ).scalar_one()
    if database != EXPECTED_DATABASE or database != settings.postgres_db:
        raise RuntimeError(f"refusing database target {database!r}; expected {EXPECTED_DATABASE!r}")
    if user != settings.postgres_user:
        raise RuntimeError("current_user does not match POSTGRES_USER")
    if not has_vector:
        raise RuntimeError("pgvector extension is not installed in hebbian_memory_mvp")


def verify_canonical_architecture_schema(engine: Engine) -> None:
    """Fail before evaluation resets or ingestion writes to an old schema."""
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        missing = sorted(CANONICAL_ARCHITECTURE_TABLES - tables)
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    if missing:
        raise SchemaCompatibilityError(
            "database schema is behind the canonical-memory application revision; "
            f"current revision={revision or 'unknown'}, required={CANONICAL_ARCHITECTURE_REVISION}, "
            f"missing tables={', '.join(missing)}. Run `uv run alembic upgrade head` against "
            "the verified HEBBIAN PostgreSQL database before retrying."
        )


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
