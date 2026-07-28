"""URL normalization and forum-only scope rules."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

_UNIDEV_HOSTS = {"unidev.com.br", "forum.unidev.com.br"}
_SESSION_QUERY_KEYS = {"sid", "phpbb3_sid"}
_ASSET_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".css",
    ".cur",
    ".eot",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".mjs",
    ".mp3",
    ".mp4",
    ".ogg",
    ".png",
    ".svg",
    ".ttf",
    ".wav",
    ".webp",
    ".woff",
    ".woff2",
}
_ATTACHMENT_EXTENSIONS = {
    ".001",
    ".7z",
    ".bz2",
    ".doc",
    ".docx",
    ".gz",
    ".odt",
    ".pdf",
    ".rar",
    ".tar",
    ".txt",
    ".xls",
    ".xlsx",
    ".zip",
}
_ATTACHMENT_PATH_RE = re.compile(r"/(?:attach(?:ment)?|download|files?)(?:/|\.|$)", re.I)


def _normalized_host(hostname: str | None) -> str:
    host = (hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def canonical_url(url: str) -> str:
    """Return a stable identity URL without replay/session noise."""

    cleaned = html.unescape(url.strip()).replace("\\075", "=")
    cleaned = re.sub(r"%5[cC]075", "=", cleaned)
    parts = urlsplit(cleaned)
    host = _normalized_host(parts.hostname)
    if not host:
        return cleaned
    port = parts.port
    netloc = host if port in {None, 80, 443} else f"{host}:{port}"
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in _SESSION_QUERY_KEYS:
            continue
        if key.lower() == "start" and value in {"", "0"}:
            continue
        query.append((key, value))
    query.sort(key=lambda item: (item[0].casefold(), item[1]))
    path = re.sub(r"/{2,}", "/", parts.path or "/").lower()
    return urlunsplit(("http", netloc, path, urlencode(query, doseq=True), ""))


def is_unidev_host(url: str) -> bool:
    return _normalized_host(urlsplit(url).hostname) in _UNIDEV_HOSTS


def is_forum_url(url: str, timestamp: str | None = None) -> bool:
    parts = urlsplit(url)
    host = _normalized_host(parts.hostname)
    path = parts.path.lower()
    if host not in _UNIDEV_HOSTS:
        return False
    if path.startswith(("/forum/", "/phpbb2/", "/phpbb3/", "/forums/", "/members/", "/user/")):
        return True
    if host == "forum.unidev.com.br":
        return timestamp is None or "2000" <= timestamp[:4] <= "2009"
    return False


def era_for_url(url: str, timestamp: str | None = None) -> str | None:
    parts = urlsplit(url)
    host = _normalized_host(parts.hostname)
    path = parts.path.lower()
    if path.startswith("/forum/"):
        return "snitz"
    if path.startswith("/phpbb2/"):
        return "phpbb2"
    if path.startswith("/phpbb3/"):
        return "phpbb3"
    if path.startswith(("/forums/", "/members/", "/user/")):
        return "community-server"
    if host == "forum.unidev.com.br":
        return "community-server" if timestamp and timestamp < "200706" else "phpbb2"
    return None


def resource_kind(url: str) -> str:
    """Classify a referenced URL as a page, asset, attachment, or external link."""

    parts = urlsplit(url)
    path = parts.path.lower()
    extension = "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    if _ATTACHMENT_PATH_RE.search(path) or extension in _ATTACHMENT_EXTENSIONS:
        return "attachment"
    if extension in _ASSET_EXTENSIONS:
        return "asset"
    if is_forum_url(url):
        return "page"
    return "external"


def resolve_references(base_url: str, references: Iterable[str]) -> tuple[str, ...]:
    """Resolve and deduplicate HTTP(S) references while retaining order."""

    found: dict[str, None] = {}
    for reference in references:
        value = html.unescape(reference).strip()
        if not value or value.startswith(("#", "data:", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, value)
        if urlsplit(absolute).scheme.lower() not in {"http", "https"}:
            continue
        found.setdefault(canonical_url(absolute), None)
    return tuple(found)
