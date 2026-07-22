import pytest

import hela_mem_zh_mvp.db as db
from hela_mem_zh_mvp.settings import Settings


def test_database_engine_uses_a_bounded_connect_timeout(monkeypatch) -> None:
    captured = {}

    def fake_create_engine(*args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(db, "create_engine", fake_create_engine)
    db.create_db_engine(Settings(postgres_password="not-a-real-secret"))
    assert captured["connect_args"] == {"connect_timeout": 10}


def test_schema_preflight_reports_missing_canonical_tables(monkeypatch) -> None:
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):  # noqa: ANN002
            return None

        def execute(self, statement):  # noqa: ARG002
            return type("Result", (), {"scalar_one_or_none": lambda self: "20260720_0004"})()

    class Engine:
        def connect(self):
            return Connection()

    monkeypatch.setattr(
        db,
        "inspect",
        lambda connection: type(
            "Inspector",
            (),
            {
                "get_table_names": lambda self: ["memories"],
                "get_columns": lambda self, table: [],  # noqa: ARG005
            },
        )(),
    )

    with pytest.raises(
        db.SchemaCompatibilityError,
            match="20260720_0004.*20260722_0008.*memory_associations",
    ):
        db.verify_canonical_architecture_schema(Engine())
