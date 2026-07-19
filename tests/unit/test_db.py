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
