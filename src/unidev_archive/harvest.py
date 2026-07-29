# pyright: reportMissingImports=false
"""Responsible, resumable retrieval of exact Wayback captures for local builds."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from unidev_archive.manifest import validate_blob_payload, validate_manifest_row

_DEFAULT_UA = "UniDevArchive/0.1 (+https://github.com/skhaz/unidev; historical preservation)"
_RETRYABLE = {429, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class DownloadStats:
    queued: int
    downloaded: int
    failed: int
    skipped: int
    bytes: int


def read_cdx_files(
    paths: Iterable[Path],
    *,
    strict: bool = True,
) -> list[dict[str, object]]:
    """Read and globally deduplicate public CDX JSON exports."""

    records: dict[tuple[str, str], dict[str, object]] = {}
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            if strict:
                raise ValueError(f"exportação CDX inválida: {path}") from error
            continue
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
            if strict:
                raise ValueError(f"estrutura CDX inválida: {path}")
            continue
        header = payload[0]
        required = {
            "timestamp",
            "original",
            "statuscode",
            "mimetype",
            "digest",
            "length",
        }
        if not required.issubset(header):
            if strict:
                missing = ", ".join(sorted(required.difference(header)))
                raise ValueError(f"campos CDX ausentes em {path}: {missing}")
            continue
        for line_number, values in enumerate(payload[1:], 2):
            if not isinstance(values, list) or len(values) != len(header):
                if strict:
                    raise ValueError(f"linha CDX inválida: {path}:{line_number}")
                continue
            source = dict(zip(header, values, strict=True))
            timestamp = source.get("timestamp")
            original = source.get("original")
            if not isinstance(timestamp, str) or not timestamp.isdigit() or len(timestamp) != 14:
                if strict:
                    raise ValueError(f"timestamp CDX inválido: {path}:{line_number}")
                continue
            if not isinstance(original, str) or not original.startswith(("http://", "https://")):
                if strict:
                    raise ValueError(f"URL CDX inválida: {path}:{line_number}")
                continue
            statuscode = _integer(source.get("statuscode"))
            length = _integer(source.get("length"))
            mimetype = source.get("mimetype")
            digest = source.get("digest")
            if (
                statuscode is None
                or length is None
                or not isinstance(mimetype, str)
                or not mimetype
                or not isinstance(digest, str)
                or len(digest) != 32
                or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for character in digest)
            ):
                if strict:
                    raise ValueError(f"metadados CDX inválidos: {path}:{line_number}")
                continue
            record: dict[str, object] = {
                "timestamp": timestamp,
                "original_url": original,
                "statuscode": statuscode,
                "mimetype": mimetype,
                "digest": digest,
                "length": length,
            }
            key = (timestamp, original)
            previous = records.get(key)
            if previous is not None and previous != record:
                if strict:
                    raise ValueError(f"captura CDX conflitante: {path}:{line_number}")
                continue
            records[key] = record
    return [records[key] for key in sorted(records)]


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def write_inventory(records: Sequence[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _compact_manifest(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    valid: dict[tuple[str, str], dict[str, object]] = {}
    with path.open(encoding="utf-8") as source:
        for line in source:
            try:
                row = json.loads(line)
                validate_manifest_row(row, path, 0)
                blob = path.parent / str(row["path"])
                raw = blob.read_bytes()
                validate_blob_payload(row, raw, blob)
                key = (str(row["timestamp"]), str(row["original_url"]))
                valid[key] = row
            except (json.JSONDecodeError, KeyError, TypeError, OSError, ValueError):
                continue
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as output:
        for row in valid.values():
            output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)
    return set(valid)


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(max(float(retry_after), 1.0), 300.0)
            except ValueError:
                pass
    return min(2.0**attempt, 120.0)


async def _fetch_one(
    client: httpx.AsyncClient,
    record: dict[str, object],
    archive: Path,
    delay: float,
    retries: int,
) -> tuple[dict[str, object] | None, dict[str, object] | None, int]:
    timestamp = str(record["timestamp"])
    original = str(record["original_url"])
    expected_digest = str(record.get("digest") or "").upper()
    replay = f"https://web.archive.org/web/{timestamp}id_/{original}"
    if len(expected_digest) != 32:
        return (
            None,
            {
                **record,
                "replay_url": replay,
                "error": "digest CDX obrigatório ou inválido",
            },
            0,
        )
    temporary_dir = archive / ".tmp"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries + 1):
        response: httpx.Response | None = None
        temporary = (
            temporary_dir / f"{hashlib.sha256(replay.encode()).hexdigest()}.{os.getpid()}.part"
        )
        digest = hashlib.sha256()
        cdx_digest = hashlib.sha1(usedforsecurity=False)
        byte_length = 0
        try:
            async with client.stream("GET", replay) as response:
                replay_identity = f"/web/{timestamp}id_/"
                if replay_identity not in str(response.url):
                    temporary.unlink(missing_ok=True)
                    return (
                        None,
                        {
                            **record,
                            "replay_url": replay,
                            "final_url": str(response.url),
                            "error": "replay redirecionou para outra captura",
                        },
                        0,
                    )
                if response.status_code != 200:
                    if response.status_code in _RETRYABLE and attempt < retries:
                        await asyncio.sleep(_retry_delay(response, attempt))
                        continue
                    await asyncio.sleep(delay)
                    return (
                        None,
                        {
                            **record,
                            "replay_url": replay,
                            "http_status": response.status_code,
                            "error": "resposta HTTP não recuperável",
                        },
                        0,
                    )
                with temporary.open("wb") as output:
                    async for chunk in response.aiter_bytes(64 * 1024):
                        output.write(chunk)
                        digest.update(chunk)
                        cdx_digest.update(chunk)
                        byte_length += len(chunk)
            actual_digest = base64.b32encode(cdx_digest.digest()).decode().rstrip("=")
            sha256 = digest.hexdigest()
            relative = Path("blobs") / sha256[:2] / sha256
            target = archive / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and _file_sha256(target) == sha256:
                temporary.unlink(missing_ok=True)
            else:
                temporary.replace(target)
            result = {
                **record,
                "source": "wayback",
                "digest_source": "cdx",
                "retrieved_length": byte_length,
                "payload_digest": actual_digest,
                "cdx_digest_matches_payload": expected_digest == actual_digest,
                "sha256": sha256,
                "path": relative.as_posix(),
                "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            await asyncio.sleep(delay)
            return result, None, byte_length
        except (httpx.HTTPError, OSError) as error:
            temporary.unlink(missing_ok=True)
            if attempt >= retries:
                return None, {**record, "replay_url": replay, "error": str(error)}, 0
            await asyncio.sleep(_retry_delay(response, attempt))
    raise AssertionError("laço de retry terminou sem resultado")


async def download_inventory(
    inventory_path: Path,
    archive: Path,
    *,
    concurrency: int = 2,
    delay: float = 0.75,
    retries: int = 5,
    user_agent: str = _DEFAULT_UA,
) -> DownloadStats:
    """Download an inventory with bounded concurrency and durable per-item progress."""

    concurrency = min(max(concurrency, 1), 8)
    archive.mkdir(parents=True, exist_ok=True)
    manifest_path = archive / "captures.jsonl"
    failures_path = archive / "download-failures.jsonl"
    existing = _compact_manifest(manifest_path)
    queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue(concurrency * 2)
    result_queue: asyncio.Queue[
        tuple[dict[str, object] | None, dict[str, object] | None, int] | None
    ] = asyncio.Queue(concurrency * 2)
    counters = {"queued": 0, "downloaded": 0, "failed": 0, "skipped": 0, "bytes": 0}

    async def produce() -> None:
        with inventory_path.open(encoding="utf-8") as source:
            for line in source:
                if not line.strip():
                    continue
                record = json.loads(line)
                key = (str(record["timestamp"]), str(record["original_url"]))
                if key in existing:
                    counters["skipped"] += 1
                    continue
                counters["queued"] += 1
                await queue.put(record)
        for _ in range(concurrency):
            await queue.put(None)

    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    timeout = httpx.Timeout(connect=30.0, read=180.0, write=30.0, pool=30.0)
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
    async with httpx.AsyncClient(
        headers=headers,
        limits=limits,
        timeout=timeout,
        follow_redirects=False,
    ) as client:

        async def worker() -> None:
            while (record := await queue.get()) is not None:
                await result_queue.put(await _fetch_one(client, record, archive, delay, retries))
            await result_queue.put(None)

        async def write_results() -> None:
            finished = 0
            with (
                manifest_path.open("a", encoding="utf-8", newline="\n") as manifest,
                failures_path.open("a", encoding="utf-8", newline="\n") as failures,
            ):
                while finished < concurrency:
                    result = await result_queue.get()
                    if result is None:
                        finished += 1
                        continue
                    row, failure, byte_length = result
                    if row is not None:
                        manifest.write(
                            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                        )
                        manifest.flush()
                        counters["downloaded"] += 1
                        counters["bytes"] += byte_length
                    elif failure is not None:
                        failures.write(
                            json.dumps(failure, ensure_ascii=False, separators=(",", ":")) + "\n"
                        )
                        failures.flush()
                        counters["failed"] += 1

        await asyncio.gather(produce(), *(worker() for _ in range(concurrency)), write_results())

    return DownloadStats(**counters)
