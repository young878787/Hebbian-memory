from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from hela_mem_zh_mvp.config import load_config
from hela_mem_zh_mvp.retrieval import workflow


def test_learning_uses_only_answer_citations_after_answer_gate(monkeypatch) -> None:
    cited_id, uncited_id = uuid4(), uuid4()
    result = SimpleNamespace(
        run_id=uuid4(),
        items=[
            SimpleNamespace(selected=True, external_id="cited", memory=SimpleNamespace(id=cited_id)),
            SimpleNamespace(
                selected=True, external_id="uncited", memory=SimpleNamespace(id=uncited_id)
            ),
        ],
        as_dict=lambda: {"items": []},
    )
    monkeypatch.setattr(workflow, "get_namespace", lambda *args, **kwargs: SimpleNamespace(id=uuid4()))
    monkeypatch.setattr(
        workflow,
        "Retriever",
        lambda *args, **kwargs: SimpleNamespace(retrieve=lambda *args, **kwargs: result),
    )
    monkeypatch.setattr(
        workflow,
        "answer_query",
        lambda *args, **kwargs: SimpleNamespace(
            answerable=True,
            citations=["cited"],
            model_dump=lambda: {"answerable": True, "citations": ["cited"]},
        ),
    )
    reinforced = Mock(return_value=1)
    monkeypatch.setattr(workflow, "reinforce_co_retrieval", reinforced)
    monkeypatch.setattr(workflow, "_write_retrieval_artifact", lambda payload: None)
    session = Mock()

    workflow.ask(session, "namespace", "query", Mock(), Mock(), load_config(), learn=True)

    assert reinforced.call_args.args[2] == [cited_id]
    assert reinforced.call_args.kwargs["citations"] == ["cited"]
    session.commit.assert_called_once_with()


def test_unanswerable_result_never_triggers_learning(monkeypatch) -> None:
    result = SimpleNamespace(run_id=uuid4(), items=[], as_dict=lambda: {"items": []})
    monkeypatch.setattr(workflow, "get_namespace", lambda *args, **kwargs: SimpleNamespace(id=uuid4()))
    monkeypatch.setattr(
        workflow,
        "Retriever",
        lambda *args, **kwargs: SimpleNamespace(retrieve=lambda *args, **kwargs: result),
    )
    monkeypatch.setattr(
        workflow,
        "answer_query",
        lambda *args, **kwargs: SimpleNamespace(
            answerable=False,
            citations=[],
            model_dump=lambda: {"answerable": False, "citations": []},
        ),
    )
    reinforced = Mock()
    monkeypatch.setattr(workflow, "reinforce_co_retrieval", reinforced)
    monkeypatch.setattr(workflow, "_write_retrieval_artifact", lambda payload: None)
    session = Mock()

    workflow.ask(session, "namespace", "query", Mock(), Mock(), load_config(), learn=True)

    reinforced.assert_not_called()
    session.commit.assert_not_called()
