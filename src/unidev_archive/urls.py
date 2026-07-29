"""URL normalization and forum-only scope rules."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

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
_PAGE_EXTENSIONS = {".asp", ".aspx", ".htm", ".html", ".php"}
_WAYBACK_RE = re.compile(
    r"^https?://web\.archive\.org/web/\d+(?:id_|im_|js_|cs_)?/(https?://.+)$",
    re.I,
)
_CASE_SENSITIVE_EXTENSIONS = _ASSET_EXTENSIONS | _ATTACHMENT_EXTENSIONS


def _normalized_host(hostname: str | None) -> str:
    host = (hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def unwrap_wayback_url(url: str) -> str:
    match = _WAYBACK_RE.match(url)
    return match.group(1) if match else url


def canonical_url(url: str) -> str:
    """Return a stable identity URL without replay/session noise."""

    cleaned = unwrap_wayback_url(html.unescape(url.strip())).replace("\\075", "=")
    cleaned = re.sub(r"%5[cC]075", "=", cleaned)
    try:
        parts = urlsplit(cleaned)
    except ValueError:
        return ""
    host = _normalized_host(parts.hostname)
    if not host:
        return cleaned
    try:
        port = parts.port
    except ValueError:
        return ""
    netloc = host if port in {None, 80, 443} else f"{host}:{port}"
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in _SESSION_QUERY_KEYS:
            continue
        if key.lower() == "start" and value in {"", "0"}:
            continue
        query.append((key, value))
    query.sort(key=lambda item: (item[0].casefold(), item[1]))
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if host in _UNIDEV_HOSTS and not path.islower():
        dot = path.rfind(".")
        suffix = path[dot:].casefold() if dot > path.rfind("/") else ""
        if suffix not in _CASE_SENSITIVE_EXTENSIONS:
            path = path.lower()
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
    if host != "forum.unidev.com.br" or (timestamp is not None and timestamp[:4] < "2000"):
        return False
    if path in {"", "/", "/default.aspx", "/index.php"}:
        return True
    return path.startswith(("/feed/", "/languages/", "/recentes/", "/search/"))


def era_for_url(url: str, timestamp: str | None = None) -> str | None:
    parts = urlsplit(url)
    host = _normalized_host(parts.hostname)
    if host not in _UNIDEV_HOSTS:
        return None
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


def is_navigable_page_url(url: str) -> bool:
    """Reject malformed relative labels while retaining real historical endpoints."""

    if not is_forum_url(url):
        return False
    try:
        path = urlsplit(url).path
    except ValueError:
        return False
    basename = path.rstrip("/").rsplit("/", 1)[-1]
    if not basename or path.endswith("/"):
        return True
    extension = "." + basename.rsplit(".", 1)[-1].casefold() if "." in basename else ""
    if extension:
        return extension in _PAGE_EXTENSIONS
    decoded_path = unquote(path) if "%" in path else path
    return not any(character.isspace() or character in '*<>"{}|' for character in decoded_path)


def resource_kind(url: str) -> str:
    """Classify a referenced URL as a page, asset, attachment, or external link."""

    parts = urlsplit(url)
    path = parts.path.lower()
    extension = "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    if _ATTACHMENT_PATH_RE.search(path) or extension in _ATTACHMENT_EXTENSIONS:
        return "attachment"
    if extension in _ASSET_EXTENSIONS:
        return "asset"
    if is_navigable_page_url(url):
        return "page"
    return "external"


def resolve_reference_sets(
    base_url: str,
    references: Iterable[str],
    asset_references: Iterable[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Resolve all references and an asset subset with one canonicalization pass."""

    assets = {html.unescape(reference).strip() for reference in asset_references}
    found: dict[str, None] = {}
    found_assets: dict[str, None] = {}
    for reference in references:
        value = html.unescape(reference).strip()
        if not value or value.startswith(("#", "data:", "javascript:", "mailto:", "tel:")):
            continue
        try:
            absolute = urljoin(base_url, value)
            scheme = urlsplit(absolute).scheme.lower()
        except ValueError:
            continue
        if scheme not in {"http", "https"}:
            continue
        canonical = canonical_url(absolute)
        if not canonical:
            continue
        found.setdefault(canonical, None)
        if value in assets:
            found_assets.setdefault(canonical, None)
    return tuple(found), tuple(found_assets)


def resolve_references(base_url: str, references: Iterable[str]) -> tuple[str, ...]:
    """Resolve and deduplicate HTTP(S) references while retaining order."""

    return resolve_reference_sets(base_url, references, ())[0]
