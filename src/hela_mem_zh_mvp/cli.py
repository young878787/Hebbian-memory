"""CLI entry point with the specified success, runtime, and config exit codes."""

from __future__ import annotations

import argparse
import json
import sys

from .config import load_config
from .db import create_db_engine, session_factory, verify_database_target
from .embedding import EmbeddingClient
from .evaluator import evaluate, write_report
from .fixtures import FixtureError, load_fixture_bundle, seed_fixtures
from .provider import GoogleProvider
from .retriever import Retriever
from .schemas import QueryScope, RetrievalMode, RunMode
from .settings import get_settings


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _smoke(component: str) -> dict:
    settings = get_settings()
    load_config()
    bundle = load_fixture_bundle("data/fixtures")
    result: dict = {"config": "ok", "fixtures": {"memories": len(bundle.memories), "queries": len(bundle.queries)}}
    if component in {"postgres", "all"}:
        if missing := settings.missing():
            raise ValueError(f"missing required configuration: {', '.join(missing)}")
        engine = create_db_engine(settings)
        verify_database_target(engine, settings)
        result["postgres"] = "ok"
    if component in {"embedding", "all"}:
        probe = EmbeddingClient(settings).smoke()
        result["embedding"] = probe.__dict__
    if component in {"provider", "all"}:
        if missing := settings.missing(include_google=True):
            raise ValueError(f"missing required configuration: {', '.join(missing)}")
        probe = GoogleProvider(settings).smoke()
        result["provider"] = {"model": probe.model, "latency_ms": probe.latency_ms, "non_empty": bool(probe.text)}
    return result


def _database_session():
    settings = get_settings()
    if missing := settings.missing():
        raise ValueError(f"missing required configuration: {', '.join(missing)}")
    engine = create_db_engine(settings)
    verify_database_target(engine, settings)
    return session_factory(engine)()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hela-mem")
    commands = parser.add_subparsers(dest="command", required=True)
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--component", choices=("postgres", "embedding", "provider", "all"), default="all")
    seed = commands.add_parser("seed")
    seed.add_argument("--fixtures", default="data/fixtures")
    seed.add_argument("--replace-fixtures", action="store_true")
    query = commands.add_parser("query")
    query.add_argument("--mode", choices=[item.value for item in RetrievalMode], required=True)
    query.add_argument("--scope", choices=[item.value for item in QueryScope], default="general")
    query.add_argument("--run-mode", choices=[item.value for item in RunMode], default="evaluation")
    query.add_argument("query")
    evaluate_command = commands.add_parser("evaluate")
    evaluate_command.add_argument("--run-mode", choices=[item.value for item in RunMode], default="evaluation")
    evaluate_command.add_argument("--output", default="results")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "smoke":
            _print_json(_smoke(args.component))
        elif args.command == "seed":
            bundle = load_fixture_bundle(args.fixtures)
            with _database_session() as session:
                count = seed_fixtures(session, bundle, EmbeddingClient(get_settings()), replace_fixtures=args.replace_fixtures)
                session.commit()
            _print_json({"seeded": count})
        elif args.command == "query":
            with _database_session() as session:
                result = Retriever(session, load_config(), EmbeddingClient(get_settings())).retrieve(
                    args.query, RetrievalMode(args.mode), QueryScope(args.scope), RunMode(args.run_mode)
                )
            _print_json(result.as_dict())
        elif args.command == "evaluate":
            bundle = load_fixture_bundle("data/fixtures")
            with _database_session() as session:
                report = evaluate(session, load_config(), EmbeddingClient(get_settings()), bundle, RunMode(args.run_mode))
            write_report(report, args.output)
            _print_json(report.summary)
        return 0
    except (ValueError, FixtureError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"runtime failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
