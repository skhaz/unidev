# pyright: reportMissingImports=false
"""Deterministic archive ingestion used locally and by GitHub Actions."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from lxml.etree import ParserError

from unidev_archive.database import ArchiveDB, CaptureRecord
from unidev_archive.encoding import decode_html
from unidev_archive.manifest import validate_blob_payload, validate_manifest_row
from unidev_archive.mirror import MirrorStats, build_mirror_site
from unidev_archive.parser import parse_css_references, parse_html_reference_sets, parse_page
from unidev_archive.urls import era_for_url, is_forum_url


@dataclass(frozen=True, slots=True)
class RebuildStats:
    captures: int
    parsed_pages: int
    extracted_posts: int
    extracted_listings: int
    site: MirrorStats


@dataclass(frozen=True, slots=True)
class ArchivePeriod:
    start: str
    end: str
    post_id: int
    posted_at: str
    capture_timestamp: str
    original_url: str
    digest: str


def _load_manifest(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    identities: set[tuple[str, str]] = set()
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
            validate_manifest_row(row, path, line_number)
            identity = (str(row["original_url"]), str(row["timestamp"]))
            if identity in identities:
                raise ValueError(f"captura duplicada em {path}:{line_number}: {identity}")
            identities.add(identity)
            rows.append(row)
    rows.sort(
        key=lambda row: (
            str(row.get("timestamp", "")),
            str(row.get("original_url", "")),
            str(row.get("sha256", "")),
        )
    )
    return rows


def _load_period(archive: Path) -> ArchivePeriod:
    path = archive / "period.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        start = str(payload["start"])
        end = str(payload["end"])
        evidence = payload["evidence"]
        period = ArchivePeriod(
            start=start,
            end=end,
            post_id=int(evidence["post_id"]),
            posted_at=str(evidence["posted_at"]),
            capture_timestamp=str(evidence["capture_timestamp"]),
            original_url=str(evidence["original_url"]),
            digest=str(evidence["digest"]),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError(f"período histórico inválido ou ausente: {path}") from error
    if (
        len(start) != 19
        or len(end) != 19
        or start > end
        or period.posted_at != end
        or len(period.capture_timestamp) != 14
        or not period.capture_timestamp.isdigit()
        or not period.original_url.startswith(("http://", "https://"))
        or not period.digest
    ):
        raise ValueError(f"limites ou evidência inválidos em {path}")
    return period


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
        source=str(row["source"]),
        requested_url=str(row["requested_url"]) if row.get("requested_url") else None,
    )


def rebuild_archive(
    manifest_path: str | Path,
    database_path: str | Path,
    output_path: str | Path,
    *,
    verify_period_evidence: bool = True,
) -> RebuildStats:
    """Verify raw blobs, extract posts, and generate the UTF-8 static site."""

    manifest = Path(manifest_path)
    database_file = Path(database_path)
    if database_file.exists():
        database_file.unlink()
    rows = _load_manifest(manifest)
    period = _load_period(manifest.parent)
    if verify_period_evidence and not any(
        str(row.get("timestamp")) == period.capture_timestamp
        and str(row.get("original_url")) == period.original_url
        and str(row.get("digest")) == period.digest
        and row.get("cdx_digest_matches_payload") is True
        for row in rows
    ):
        raise ValueError("captura que comprova o fim do período não está no manifesto")
    records = [_capture_record(row) for row in rows]
    parsed_pages = 0
    extracted_posts = 0
    extracted_listings = 0

    with ArchiveDB(database_file, defer_stats=True) as database:
        database.initialize()
        database.add_captures(records)
        capture_ids = database.capture_ids()
        for row, record in zip(rows, records, strict=True):
            relative_path = Path(str(row["path"]))
            raw_path = (manifest.parent / relative_path).resolve()
            if not raw_path.is_relative_to(manifest.parent.resolve()):
                raise ValueError(f"blob fora do arquivo: {relative_path}")
            raw = raw_path.read_bytes()
            validate_blob_payload(row, raw, raw_path)
            sha256 = str(row["sha256"])
            capture_id = capture_ids[(record.original_url, record.timestamp)]
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
            if record.mimetype == "text/css":
                css_references = parse_css_references(raw, record.original_url)
                database.add_references(
                    capture_id,
                    css_references,
                    css_references,
                )
                continue
            if record.mimetype not in {"text/html", "application/xhtml+xml"}:
                continue
            if not is_forum_url(record.original_url, record.timestamp):
                continue
            try:
                page = parse_page(
                    raw,
                    record.original_url,
                    era_for_url(record.original_url, record.timestamp),
                )
            except (ParserError, TypeError, ValueError):
                references, asset_references = parse_html_reference_sets(
                    raw,
                    record.original_url,
                )
                database.add_references(
                    capture_id,
                    references,
                    asset_references,
                )
                continue
            page = replace(
                page,
                posts=tuple(
                    post
                    for post in page.posts
                    if post.posted_at is not None and period.start <= post.posted_at <= period.end
                ),
                listings=tuple(
                    listing
                    for listing in page.listings
                    if any(
                        date is not None and period.start <= date <= period.end
                        for date in (listing.created_at, listing.last_posted_at)
                    )
                ),
            )
            parsed_pages += 1
            extracted_posts += database.ingest_page(capture_id, record.timestamp, page)
            extracted_listings += database.ingest_listings(capture_id, record.timestamp, page)
            database.add_references(
                capture_id,
                page.references,
                page.asset_references,
            )
        database.resolve_ingest_relations()
        database.refresh_all_stats()
        if verify_period_evidence:
            terminal_post = database.connection.execute(
                """
                SELECT 1
                FROM posts AS p
                JOIN post_sources AS ps ON ps.post_pk=p.post_pk
                JOIN captures AS c ON c.capture_id=ps.capture_id
                WHERE p.historical_id=? AND p.posted_at=?
                  AND c.timestamp=? AND c.original_url=? AND c.cdx_digest=?
                """,
                (
                    period.post_id,
                    period.posted_at,
                    period.capture_timestamp,
                    period.original_url,
                    period.digest,
                ),
            ).fetchone()
            if terminal_post is None:
                raise ValueError("mensagem que comprova o fim do período não foi extraída")
        site_stats = build_mirror_site(
            database,
            manifest.parent,
            output_path,
            period_start=period.start,
            period_end=period.end,
        )

    return RebuildStats(
        captures=len(records),
        parsed_pages=parsed_pages,
        extracted_posts=extracted_posts,
        extracted_listings=extracted_listings,
        site=site_stats,
    )
