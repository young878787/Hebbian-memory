"""Small fixed-surface CLI for the v0.5 shared-memory pipeline."""

from __future__ import annotations

import argparse
import json
import os
import sys

from sqlalchemy import select

from .config import load_config
from .db import create_db_engine, session_factory, verify_database_target
from .embedding import EmbeddingClient
from .fixtures import load_fixture_bundle
from .memory_store import get_namespace
from .models import MemoryCandidate, MemoryResolutionDecision
from .pipeline import ask, evaluate, ingest
from .provider import GoogleProvider
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
    resolve = commands.add_parser("resolve")
    resolve.add_argument("--namespace", required=True)
    resolve.add_argument("--dry-run", action="store_true", default=True)
    resolve.add_argument("--apply", action="store_true")
    report = commands.add_parser("resolution-report")
    report.add_argument("--namespace", required=True)
    backfill = commands.add_parser("backfill-resolution")
    backfill.add_argument("--namespace", required=True)
    backfill.add_argument("--report-only", action="store_true", default=True)
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
        elif args.command in {"resolve", "resolution-report", "backfill-resolution"}:
            if args.command == "resolve" and args.apply:
                raise ValueError("live resolution apply is disabled; review resolution-report first")
            with _database_session(require_google=False) as session:
                namespace = get_namespace(session, args.namespace, create=False)
                candidates = session.scalars(
                    select(MemoryCandidate).where(MemoryCandidate.namespace_id == namespace.id)
                ).all()
                decisions = session.scalars(
                    select(MemoryResolutionDecision).where(
                        MemoryResolutionDecision.namespace_id == namespace.id
                    )
                ).all()
                _print_json(
                    {
                        "namespace": args.namespace,
                        "candidate_statuses": {
                            status: sum(item.status == status for item in candidates)
                            for status in ("pending", "resolving", "resolved", "deferred", "failed")
                        },
                        "decisions": [
                            {"action": item.action, "validation_status": item.validation_status}
                            for item in decisions
                        ],
                        "apply": False,
                    }
                )
        return 0
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"runtime failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
