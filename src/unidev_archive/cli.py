# pyright: reportMissingImports=false
"""Command-line interface. All commands are build-time; the published site is static."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from unidev_archive.harvest import download_inventory, read_cdx_files, write_inventory
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
    rebuild.add_argument("--manifest", type=Path, default=Path(".build/acervo/captures.jsonl"))
    rebuild.add_argument("--database", type=Path, default=Path(".build/archive.sqlite3"))
    rebuild.add_argument("--output", type=Path, default=Path("dist"))

    inventory = subcommands.add_parser(
        "make-inventory",
        help="consolida exportações CDX em um inventário global sem duplicatas",
    )
    inventory.add_argument("--cdx-dir", type=Path, required=True)
    inventory.add_argument("--output", type=Path, required=True)

    download = subcommands.add_parser(
        "download",
        help="baixa capturas exatas para o arquivo content-addressed",
    )
    download.add_argument("--inventory", type=Path, required=True)
    download.add_argument("--archive", type=Path, default=Path("archive"))
    download.add_argument("--concurrency", type=int, default=2)
    download.add_argument("--delay", type=float, default=0.75)
    download.add_argument("--retries", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "rebuild":
        stats = rebuild_archive(arguments.manifest, arguments.database, arguments.output)
        print(json.dumps(asdict(stats), ensure_ascii=False, indent=2))
        return 0
    if arguments.command == "make-inventory":
        records = read_cdx_files(sorted(arguments.cdx_dir.glob("*.json")))
        write_inventory(records, arguments.output)
        print(json.dumps({"captures": len(records), "output": str(arguments.output)}))
        return 0
    if arguments.command == "download":
        stats = asyncio.run(
            download_inventory(
                arguments.inventory,
                arguments.archive,
                concurrency=arguments.concurrency,
                delay=arguments.delay,
                retries=arguments.retries,
            )
        )
        print(json.dumps(asdict(stats), ensure_ascii=False, indent=2))
        return 0 if stats.failed == 0 else 2
    raise AssertionError(arguments.command)
