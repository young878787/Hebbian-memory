"""Static dependency checks for the incremental feature-slice migration."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "hela_mem_zh_mvp"
PACKAGE_NAME = "hela_mem_zh_mvp"
GENERIC_MODULE_NAMES = {"common.py", "helpers.py", "utils.py"}
LEGACY_FACADE_MODULES = {
    "answerer.py",
    "candidate_search.py",
    "edges.py",
    "embedding.py",
    "evaluator.py",
    "extractor.py",
    "fixtures.py",
    "ingestion.py",
    "judge.py",
    "live_evaluation.py",
    "memory_store.py",
    "models.py",
    "normalization.py",
    "pipeline.py",
    "provider.py",
    "resolution_store.py",
    "resolver.py",
    "retriever.py",
    "schemas.py",
    "single_evaluation.py",
}


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join([PACKAGE_NAME, *parts])


def _import_targets(module: str, path: Path) -> Iterable[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package_parts = module.split(".")[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_parts[: len(package_parts) - (node.level - 1)]
                target_parts = [*base, *(node.module or "").split(".")]
                target = ".".join(part for part in target_parts if part)
            else:
                target = node.module or ""
            if target:
                yield node.lineno, target


def _feature_modules(feature: str) -> list[tuple[str, Path]]:
    root = PACKAGE_ROOT / feature
    if not root.is_dir():
        return []
    return [(_module_name(path), path) for path in root.rglob("*.py") if path.name != "__init__.py"]


def _violations(modules: list[tuple[str, Path]], forbidden_prefixes: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    for module, path in modules:
        for line, target in _import_targets(module, path):
            if target.startswith(forbidden_prefixes):
                violations.append(f"{path.relative_to(PACKAGE_ROOT)}:{line} imports {target}")
    return violations


def test_no_generic_catch_all_modules() -> None:
    unexpected = sorted(
        path.relative_to(PACKAGE_ROOT)
        for path in PACKAGE_ROOT.rglob("*.py")
        if path.name in GENERIC_MODULE_NAMES
    )
    assert not unexpected, f"generic catch-all modules are forbidden: {unexpected}"


def test_no_legacy_compatibility_facades_remain() -> None:
    remaining = sorted(
        path.name
        for path in PACKAGE_ROOT.iterdir()
        if path.is_file() and path.name in LEGACY_FACADE_MODULES
    )
    assert not remaining, f"legacy compatibility facades must be deleted: {remaining}"


def test_feature_package_dependencies_follow_plan_when_modules_exist() -> None:
    """Empty boundary packages remain valid during Phase 1; real modules are checked."""
    violations = [
        *_violations(
            _feature_modules("providers"),
            (
                f"{PACKAGE_NAME}.persistence",
                f"{PACKAGE_NAME}.ingestion",
                f"{PACKAGE_NAME}.retrieval",
                f"{PACKAGE_NAME}.evaluation",
            ),
        ),
        *_violations(
            _feature_modules("persistence"),
            (
                f"{PACKAGE_NAME}.ingestion.service",
                f"{PACKAGE_NAME}.retrieval.service",
                f"{PACKAGE_NAME}.evaluation",
                f"{PACKAGE_NAME}.providers.google",
            ),
        ),
        *_violations(_feature_modules("ingestion"), (f"{PACKAGE_NAME}.retrieval",)),
        *_violations(_feature_modules("retrieval"), (f"{PACKAGE_NAME}.ingestion",)),
    ]
    assert not violations, "forbidden feature dependency:\n" + "\n".join(violations)
