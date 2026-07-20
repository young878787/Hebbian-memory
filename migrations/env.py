from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from hela_mem_zh_mvp.persistence.models import Base
from hela_mem_zh_mvp.settings import EXPECTED_DATABASE, get_settings

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return get_settings().database_url().render_as_string(hide_password=False)


def _guard(connection) -> None:
    database = connection.execute(text("SELECT current_database()")).scalar_one()
    if database != EXPECTED_DATABASE:
        raise RuntimeError(f"refusing Alembic target {database!r}; expected {EXPECTED_DATABASE!r}")


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        _guard(connection)
        # `_guard()` issues a SELECT, which opens an implicit SQLAlchemy transaction.
        # End that read-only transaction so Alembic owns and commits its DDL transaction.
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
