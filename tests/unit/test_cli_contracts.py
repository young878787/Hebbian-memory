"""Characterisation tests for the public CLI parser and exit-code surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hela_mem_zh_mvp import cli
from hela_mem_zh_mvp.evaluation.single_e2e import (
    DEFAULT_INPUT_PATH,
    DEFAULT_QUERY_ID,
    DEFAULT_QUERY_LIMIT,
)


def test_parser_preserves_supported_command_surface() -> None:
    parser = cli.build_parser()

    assert parser.parse_args(["smoke"]).command == "smoke"
    assert parser.parse_args(["ingest"]).command == "ingest"
    assert parser.parse_args(["evaluate"]).command == "evaluate"
    assert parser.parse_args(["architecture-backfill", "--namespace", "test-space"]).command == "architecture-backfill"
    assert parser.parse_args(["lifecycle-report", "--namespace", "test-space"]).command == "lifecycle-report"
    assert parser.parse_args(["rebuild-graph", "--namespace", "test-space"]).command == "rebuild-graph"

    ask = parser.parse_args(["ask", "查詢", "--learn"])
    assert ask.command == "ask"
    assert ask.query == "查詢"
    assert ask.learn is True

    for command in ("run", "single-e2e-evaluate"):
        args = parser.parse_args([command])
        assert args.input == DEFAULT_INPUT_PATH
        assert args.query is None
        assert args.query_id == DEFAULT_QUERY_ID
        assert args.query_limit == DEFAULT_QUERY_LIMIT


def test_parser_preserves_resolution_command_defaults() -> None:
    parser = cli.build_parser()

    resolve = parser.parse_args(["resolve", "--namespace", "test-space"])
    assert resolve.dry_run is True
    assert resolve.apply is False

    report = parser.parse_args(["resolution-report", "--namespace", "test-space"])
    assert report.namespace == "test-space"

    backfill = parser.parse_args(["backfill-resolution", "--namespace", "test-space"])
    assert backfill.report_only is True


def test_parser_rejects_missing_required_arguments() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit, match="2"):
        parser.parse_args(["ask"])
    with pytest.raises(SystemExit, match="2"):
        parser.parse_args(["resolve"])


def test_main_preserves_json_success_and_error_exit_codes(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: object())
    monkeypatch.setattr(cli, "_smoke", lambda: {"status": "ok"})

    assert cli.main(["smoke"]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "ok"}

    def raise_configuration_error() -> dict:
        raise ValueError("missing setting")

    monkeypatch.setattr(cli, "_smoke", raise_configuration_error)
    assert cli.main(["smoke"]) == 2
    assert "configuration error: missing setting" in capsys.readouterr().err

    def raise_runtime_error() -> dict:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(cli, "_smoke", raise_runtime_error)
    assert cli.main(["smoke"]) == 1
    assert "runtime failure: RuntimeError: provider unavailable" in capsys.readouterr().err


def test_parser_preserves_path_coercion_for_single_e2e_input() -> None:
    args = cli.build_parser().parse_args(["run", "--input", "data/custom.jsonl"])
    assert args.input == Path("data/custom.jsonl")
