# pyright: reportMissingImports=false
"""Command-line interface. All commands are build-time; the published site is static."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from unidev_archive.pipeline import rebuild_archive


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unidev-archive",
        description="Restaura e gera o arquivo estático do fórum UniDev.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    rebuild = subcommands.add_parser(
        "rebuild",
        help="verifica os blobs, extrai o fórum e gera o site UTF-8",
    )
    rebuild.add_argument("--manifest", type=Path, default=Path("archive/captures.jsonl"))
    rebuild.add_argument("--database", type=Path, default=Path(".build/archive.sqlite3"))
    rebuild.add_argument("--output", type=Path, default=Path("dist"))
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "rebuild":
        stats = rebuild_archive(arguments.manifest, arguments.database, arguments.output)
        print(json.dumps(asdict(stats), ensure_ascii=False, indent=2))
        return 0
    raise AssertionError(arguments.command)
