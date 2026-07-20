import importlib.util
from pathlib import Path


def _project_main_module():
    path = Path(__file__).parents[2] / "main.py"
    spec = importlib.util.spec_from_file_location("project_main", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_root_main_runs_full_pipeline_by_default(monkeypatch) -> None:
    project_main = _project_main_module()
    calls: list[list[str]] = []
    monkeypatch.setattr(project_main, "cli_main", lambda argv: calls.append(argv) or 0)

    assert project_main.main([]) == 0
    assert project_main.main(["smoke"]) == 0
    assert calls == [["run"], ["smoke"]]
