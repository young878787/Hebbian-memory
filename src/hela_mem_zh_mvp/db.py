"""Database creation and target guards."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .settings import EXPECTED_DATABASE, Settings


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


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
