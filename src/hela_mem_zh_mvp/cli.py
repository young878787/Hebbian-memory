"""Small fixed-surface CLI for the v0.5 shared-memory pipeline."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from .config import load_config
from .db import (
    create_db_engine,
    session_factory,
    verify_canonical_architecture_schema,
    verify_database_target,
)
from .evaluation.fixtures import load_fixture_bundle
from .evaluation.live_resolver import run_live_resolver_evaluation
from .evaluation.single_e2e import (
    DEFAULT_INPUT_PATH,
    DEFAULT_QUERY_ID,
    DEFAULT_QUERY_LIMIT,
    run_single_e2e_evaluation,
)
from .evaluation.standard import evaluate
from .graph.projection import rebuild_projection
from .ingestion.workflow import ingest
from .lifecycle.workflow import lifecycle_report
from .persistence.backfill import canonical_backfill_report
from .persistence.namespaces import get_namespace
from .persistence.resolution_reports import resolution_report
from .providers.embedding import EmbeddingClient
from .providers.google import GoogleProvider
from .retrieval.workflow import ask
from .settings import get_settings


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _namespace_from_environment() -> str:
    namespace = os.environ.get("HEBBIAN_NAMESPACE", "").strip()
    if not namespace:
        raise ValueError("HEBBIAN_NAMESPACE is required for ingest and ask")
    return namespace


def _smoke() -> dict:
    settings = get_settings()
    load_config()
    bundle = load_fixture_bundle("data/fixtures")
    if missing := settings.missing(include_google=True):
        raise ValueError(f"missing required configuration: {', '.join(missing)}")
    engine = create_db_engine(settings)
    verify_database_target(engine, settings)
    verify_canonical_architecture_schema(engine)
    embedding = EmbeddingClient(settings).smoke()
    provider = GoogleProvider(settings).smoke()
    return {
        "config": "ok",
        "fixtures": {"queries": len(bundle.queries)},
        "postgres": "ok",
        "embedding": embedding.__dict__,
        "provider": {
            "model": provider.model,
            "latency_ms": provider.latency_ms,
            "non_empty": bool(provider.text),
        },
    }


def _database_session(*, require_google: bool = True):
    settings = get_settings()
    if missing := settings.missing(include_google=require_google):
        raise ValueError(f"missing required configuration: {', '.join(missing)}")
    engine = create_db_engine(settings)
    verify_database_target(engine, settings)
    verify_canonical_architecture_schema(engine)
    return session_factory(engine)()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hela-mem")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("smoke")
    commands.add_parser("ingest")
    ask_command = commands.add_parser("ask")
    ask_command.add_argument("query")
    ask_command.add_argument("--learn", action="store_true")
    commands.add_parser("evaluate")
    commands.add_parser("live-resolver-evaluate")
    for name in ("run", "single-e2e-evaluate"):
        run = commands.add_parser(name)
        run.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
        run.add_argument("--query")
        run.add_argument("--query-id", default=DEFAULT_QUERY_ID)
        run.add_argument("--query-limit", type=int, default=DEFAULT_QUERY_LIMIT)
    resolve = commands.add_parser("resolve")
    resolve.add_argument("--namespace", required=True)
    resolve.add_argument("--dry-run", action="store_true", default=True)
    resolve.add_argument("--apply", action="store_true")
    report = commands.add_parser("resolution-report")
    report.add_argument("--namespace", required=True)
    backfill = commands.add_parser("backfill-resolution")
    backfill.add_argument("--namespace", required=True)
    backfill.add_argument("--report-only", action="store_true", default=True)
    architecture_backfill = commands.add_parser("architecture-backfill")
    architecture_backfill.add_argument("--namespace", required=True)
    lifecycle = commands.add_parser("lifecycle-report")
    lifecycle.add_argument("--namespace", required=True)
    graph = commands.add_parser("rebuild-graph")
    graph.add_argument("--namespace", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = get_settings()
        if args.command == "smoke":
            _print_json(_smoke())
        elif args.command == "ingest":
            with _database_session() as session:
                _print_json(
                    ingest(
                        session,
                        _namespace_from_environment(),
                        GoogleProvider(settings),
                        EmbeddingClient(settings),
                        extractor_model=settings.google_model,
                    )
                )
        elif args.command == "ask":
            with _database_session() as session:
                _print_json(
                    ask(
                        session,
                        _namespace_from_environment(),
                        args.query,
                        GoogleProvider(settings),
                        EmbeddingClient(settings),
                        load_config(),
                        learn=args.learn,
                    )
                )
        elif args.command == "evaluate":
            # Live provider and served embedding checks happen before evaluate can reset its reserved namespace.
            GoogleProvider(settings).smoke()
            EmbeddingClient(settings).served_model_id()
            with _database_session() as session:
                summary = evaluate(
                    session,
                    GoogleProvider(settings),
                    EmbeddingClient(settings),
                    load_config(),
                    extractor_model=settings.google_model,
                )
            _print_json({"summary": "results/pipeline/summary.json", "status": summary["status"]})
        elif args.command == "live-resolver-evaluate":
            with _database_session() as session:
                summary = run_live_resolver_evaluation(
                    session,
                    GoogleProvider(settings),
                    EmbeddingClient(settings),
                    extractor_model=settings.google_model,
                )
            _print_json(
                {"summary": "results/live_resolver/summary.json", "status": summary["status"]}
            )
        elif args.command in {"run", "single-e2e-evaluate"}:
            with _database_session() as session:
                summary = run_single_e2e_evaluation(
                    session,
                    GoogleProvider(settings),
                    EmbeddingClient(settings),
                    load_config(),
                    extractor_model=settings.google_model,
                    input_path=args.input,
                    query=args.query,
                    query_id=args.query_id,
                    query_limit=args.query_limit,
                )
            _print_json({"summary": "results/summary.json", "status": summary["status"]})
            return 0 if summary["status"] == "PASS" else 1
        elif args.command in {"resolve", "resolution-report", "backfill-resolution"}:
            if args.command == "resolve" and args.apply:
                raise ValueError(
                    "live resolution apply is disabled; review resolution-report first"
                )
            with _database_session(require_google=False) as session:
                _print_json(resolution_report(session, args.namespace))
        elif args.command == "architecture-backfill":
            with _database_session(require_google=False) as session:
                _print_json(canonical_backfill_report(session, args.namespace))
        elif args.command == "lifecycle-report":
            with _database_session(require_google=False) as session:
                namespace = get_namespace(session, args.namespace, create=False)
                _print_json(lifecycle_report(session, namespace.id, now=datetime.now(UTC)))
        elif args.command == "rebuild-graph":
            with _database_session(require_google=False) as session:
                namespace = get_namespace(session, args.namespace, create=False)
                with session.begin():
                    _print_json(rebuild_projection(session, namespace.id, load_config().snapshot()))
        return 0
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"runtime failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
