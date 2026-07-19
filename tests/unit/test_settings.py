import pytest

from hela_mem_zh_mvp.settings import EXPECTED_DATABASE, Settings


def test_database_url_rejects_an_unexpected_database() -> None:
    settings = Settings(postgres_db="another_project", postgres_password="not-a-real-secret")
    with pytest.raises(ValueError, match="POSTGRES_DB"):
        settings.database_url()


def test_expected_database_name_is_fixed() -> None:
    assert EXPECTED_DATABASE == "hebbian_memory_mvp"
