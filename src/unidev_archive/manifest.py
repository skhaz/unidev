"""Shared structural and payload validation for capture manifests."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path, PurePosixPath

_ALLOWED_SOURCES = {"commoncrawl", "wayback", "wayback-availability"}
_BASE32 = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
_HEXADECIMAL = frozenset("0123456789abcdef")


def optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def payload_digest(raw: bytes) -> str:
    return base64.b32encode(hashlib.sha1(raw, usedforsecurity=False).digest()).decode().rstrip("=")


def validate_manifest_row(row: dict[str, object], path: Path, line_number: int) -> None:
    timestamp = str(row.get("timestamp", ""))
    original_url = str(row.get("original_url", ""))
    sha256 = str(row.get("sha256", ""))
    digest = str(row.get("digest", ""))
    computed_digest = str(row.get("payload_digest", ""))
    source = str(row.get("source", ""))
    requested_url = row.get("requested_url")
    digest_matches = row.get("cdx_digest_matches_payload")
    relative = PurePosixPath(str(row.get("path", "")))
    invalid = (
        len(timestamp) != 14
        or not timestamp.isdigit()
        or not original_url.startswith(("http://", "https://"))
        or len(sha256) != 64
        or any(character not in _HEXADECIMAL for character in sha256)
        or len(digest) != 32
        or any(character not in _BASE32 for character in digest)
        or len(computed_digest) != 32
        or any(character not in _BASE32 for character in computed_digest)
        or relative.is_absolute()
        or ".." in relative.parts
        or relative != PurePosixPath("blobs", sha256[:2], sha256)
        or not row.get("mimetype")
        or source not in _ALLOWED_SOURCES
        or (
            source == "wayback-availability"
            and (
                not isinstance(requested_url, str)
                or not requested_url.startswith(("http://", "https://"))
            )
        )
        or (source != "wayback-availability" and requested_url is not None)
        or (source == "wayback" and digest_matches is not True)
        or (
            source == "commoncrawl"
            and (digest_matches is not True or row.get("source_record_complete") is not True)
        )
        or (
            source == "wayback-availability"
            and (
                digest_matches is not None
                or row.get("digest_source") != "computed-payload"
                or digest != computed_digest
            )
        )
        or optional_int(row.get("statuscode")) is None
        or optional_int(row.get("length")) is None
        or optional_int(row.get("retrieved_length")) is None
    )
    if invalid:
        raise ValueError(f"registro inválido em {path}:{line_number}")


def validate_blob_payload(row: dict[str, object], raw: bytes, raw_path: Path) -> None:
    sha256 = hashlib.sha256(raw).hexdigest()
    expected_sha256 = str(row["sha256"])
    if sha256 != expected_sha256:
        raise ValueError(
            f"SHA-256 inválido para {raw_path}: esperado {expected_sha256}, obtido {sha256}"
        )
    computed_digest = payload_digest(raw)
    if computed_digest != row["payload_digest"]:
        raise ValueError(
            f"digest do payload inválido para {raw_path}: "
            f"esperado {row['payload_digest']}, obtido {computed_digest}"
        )
    source = str(row["source"])
    if source == "wayback-availability":
        if row["cdx_digest_matches_payload"] is not None or str(row["digest"]) != computed_digest:
            raise ValueError(f"atestado de digest inválido para {raw_path}")
    elif row["cdx_digest_matches_payload"] is not True or str(row["digest"]) != computed_digest:
        raise ValueError(f"atestado de digest inválido para {raw_path}")
    if optional_int(row.get("retrieved_length")) != len(raw):
        raise ValueError(f"tamanho recuperado inválido para {raw_path}")
