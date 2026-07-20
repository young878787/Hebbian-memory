"""Convenient project-root entry point for the complete live pipeline."""

from __future__ import annotations

import sys

from hela_mem_zh_mvp.cli import main as cli_main


def main(argv: list[str] | None = None) -> int:
    """Run the complete pipeline by default; forward explicit CLI commands unchanged."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    return cli_main(arguments or ["run"])


if __name__ == "__main__":
    raise SystemExit(main())
