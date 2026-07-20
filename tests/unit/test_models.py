from hela_mem_zh_mvp.models import MemoryEntity


def test_memory_entity_lookup_uses_its_declared_primary_key() -> None:
    assert [column.name for column in MemoryEntity.__table__.primary_key.columns] == [
        "memory_id",
        "entity_id",
    ]
