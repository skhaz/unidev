# pyright: reportMissingImports=false
"""Deterministic archive ingestion used locally and by GitHub Actions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

from unidev_archive.database import ArchiveDB, CaptureRecord
from unidev_archive.encoding import decode_html
from unidev_archive.parser import parse_page
from unidev_archive.site import BuildStats, build_site


@dataclass(frozen=True, slots=True)
class RebuildStats:
    captures: int
    parsed_pages: int
    extracted_posts: int
    extracted_listings: int
    site: BuildStats


def _load_manifest(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"JSON inválido em {path}:{line_number}: {error}") from error
            if not isinstance(row, dict):
                raise ValueError(f"registro deve ser um objeto em {path}:{line_number}")
            rows.append(row)
    return rows


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, str)) and not isinstance(value, bool):
        return int(value)
    raise ValueError(f"inteiro inválido no manifesto: {value!r}")


def _capture_record(row: dict[str, object]) -> CaptureRecord:
    return CaptureRecord(
        timestamp=str(row["timestamp"]),
        original_url=str(row["original_url"]),
        statuscode=_optional_int(row.get("statuscode")),
        mimetype=str(row["mimetype"]) if row.get("mimetype") else None,
        digest=str(row["digest"]) if row.get("digest") else None,
        length=_optional_int(row.get("length")),
    )


def rebuild_archive(
    manifest_path: str | Path,
    database_path: str | Path,
    output_path: str | Path,
) -> RebuildStats:
    """Verify raw blobs, extract posts, and generate the UTF-8 static site."""

    manifest = Path(manifest_path)
    database_file = Path(database_path)
    if database_file.exists():
        database_file.unlink()
    rows = _load_manifest(manifest)
    records = [_capture_record(row) for row in rows]
    parsed_pages = 0
    extracted_posts = 0
    extracted_listings = 0

    with ArchiveDB(database_file) as database:
        database.initialize()
        database.add_captures(records)
        for row, record in zip(rows, records, strict=True):
            relative_path = Path(str(row["path"]))
            raw_path = manifest.parent / relative_path
            raw = raw_path.read_bytes()
            sha256 = hashlib.sha256(raw).hexdigest()
            expected_sha256 = str(row.get("sha256") or sha256)
            if sha256 != expected_sha256:
                raise ValueError(
                    f"SHA-256 inválido para {raw_path}: esperado {expected_sha256}, obtido {sha256}"
                )
            capture_id = database.capture_id(record.original_url, record.timestamp)
            source_encoding = None
            if (record.mimetype or "").startswith(("text/", "application/xhtml")):
                source_encoding = decode_html(raw).source_encoding
            database.record_blob(
                capture_id,
                sha256,
                relative_path.as_posix(),
                len(raw),
                record.mimetype,
                source_encoding,
            )
            if record.mimetype not in {"text/html", "application/xhtml+xml"}:
                continue
            try:
                page = parse_page(raw, record.original_url)
            except (ValueError, TypeError):
                continue
            page = replace(
                page,
                posts=tuple(
                    post
                    for post in page.posts
                    if post.posted_at is not None and "2000-01-01" <= post.posted_at < "2010-01-01"
                ),
                listings=tuple(
                    listing
                    for listing in page.listings
                    if any(
                        date is not None and "2000-01-01" <= date < "2010-01-01"
                        for date in (listing.created_at, listing.last_posted_at)
                    )
                ),
            )
            parsed_pages += 1
            extracted_posts += database.ingest_page(capture_id, page)
            extracted_listings += database.ingest_listings(capture_id, page)
            database.add_references(capture_id, page.references)
        site_stats = build_site(database, output_path)

    return RebuildStats(
        captures=len(records),
        parsed_pages=parsed_pages,
        extracted_posts=extracted_posts,
        extracted_listings=extracted_listings,
        site=site_stats,
    )
