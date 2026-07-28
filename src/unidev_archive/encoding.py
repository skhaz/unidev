"""Lossless decoding helpers for the mixed encodings used by the old forum."""

from __future__ import annotations

import codecs
import re
import unicodedata
from dataclasses import dataclass

_CHARSET_RE = re.compile(
    rb"charset\s*=\s*[\"']?\s*([a-zA-Z0-9._-]+)",
    flags=re.IGNORECASE,
)
_MOJIBAKE_RUN_RE = re.compile(r"(?:Ã.|Â.|â..|ð...)+")


@dataclass(frozen=True, slots=True)
class DecodedDocument:
    """Decoded text and the strategy that produced it."""

    text: str
    source_encoding: str


def _declared_encoding(raw: bytes) -> str | None:
    match = _CHARSET_RE.search(raw[:8192])
    if not match:
        return None
    encoding = match.group(1).decode("ascii", "ignore").lower()
    if encoding in {"iso-8859-1", "iso8859-1", "latin1", "latin-1", "windows-1252", "cp1252"}:
        return "windows-1252"
    if encoding in {"utf8", "utf-8"}:
        return "utf-8"
    return encoding


def _decode_cp1252_bytewise(raw: bytes) -> str:
    try:
        return raw.decode("windows-1252")
    except UnicodeDecodeError:
        # Five C1 bytes are undefined in Windows-1252. Latin-1 preserves them
        # instead of silently replacing historical bytes.
        return "".join(
            bytes((value,)).decode("windows-1252")
            if value not in {0x81, 0x8D, 0x8F, 0x90, 0x9D}
            else chr(value)
            for value in raw
        )


def _decode_utf8_with_cp1252_fallback(raw: bytes) -> tuple[str, bool]:
    """Decode valid UTF-8 runs while preserving isolated legacy bytes."""

    chunks: list[str] = []
    remaining = raw
    mixed = False
    while remaining:
        try:
            chunks.append(remaining.decode("utf-8"))
            break
        except UnicodeDecodeError as error:
            mixed = True
            if error.start:
                chunks.append(remaining[: error.start].decode("utf-8"))
            end = max(error.end, error.start + 1)
            chunks.append(_decode_cp1252_bytewise(remaining[error.start : end]))
            remaining = remaining[end:]
    return "".join(chunks), mixed


def _repair_mojibake(text: str) -> str:
    def repair(match: re.Match[str]) -> str:
        value = match.group(0)
        try:
            candidate = value.encode("windows-1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return value
        return candidate

    return _MOJIBAKE_RUN_RE.sub(repair, text)


def decode_html(raw: bytes) -> DecodedDocument:
    """Decode archived HTML and normalize it to UTF-8-compatible NFC text.

    UniDev's phpBB3 pages are mostly UTF-8 but contain occasional raw
    Windows-1252 bytes. A strict whole-document fallback to Windows-1252 would
    turn correct Portuguese into mojibake, so valid UTF-8 runs are retained and
    only invalid bytes use the legacy mapping.
    """

    declared = _declared_encoding(raw)
    if declared == "windows-1252":
        text = _decode_cp1252_bytewise(raw)
        source = declared
    elif declared == "utf-8" or declared is None:
        text, mixed = _decode_utf8_with_cp1252_fallback(raw)
        source = "utf-8/windows-1252-mixed" if mixed else "utf-8"
    else:
        try:
            codecs.lookup(declared)
            text = raw.decode(declared)
            source = declared
        except (LookupError, UnicodeDecodeError):
            text, mixed = _decode_utf8_with_cp1252_fallback(raw)
            source = "utf-8/windows-1252-mixed" if mixed else "utf-8"

    return DecodedDocument(
        text=unicodedata.normalize("NFC", _repair_mojibake(text)),
        source_encoding=source,
    )
