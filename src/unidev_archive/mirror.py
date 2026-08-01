# pyright: reportMissingImports=false
"""Emit a local, faithful, read-only mirror from verified historical captures."""

from __future__ import annotations

import bisect
import hashlib
import json
import posixpath
import re
import shutil
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import chain
from pathlib import Path, PurePosixPath
from typing import Protocol, cast
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from lxml import html as lxml_html

from unidev_archive.css import css_references, has_unsupported_network_syntax
from unidev_archive.database import ArchiveDB
from unidev_archive.entities import plan_entity_fallbacks, write_entity_pages
from unidev_archive.markup import local_name
from unidev_archive.preservation import preserve_document, preserve_stylesheet, preserve_svg
from unidev_archive.routing import RouteRegistry, is_inert_action_url, static_route
from unidev_archive.srcset import parse_srcset
from unidev_archive.urls import canonical_url, era_for_url, is_forum_url


class _Row(Protocol):
    def __getitem__(self, key: str) -> object: ...


def _row_int(row: _Row, key: str) -> int:
    try:
        return int(str(row[key]))
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise MirrorIntegrityError(f"valor inteiro inválido na coluna {key}") from error


def _remove_tree(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except OSError as error:
        raise MirrorIntegrityError(f"não foi possível remover {path}") from error


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MIME_EXTENSIONS = {
    "text/css": ".css",
    "application/javascript": ".js",
    "text/javascript": ".js",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/svg+xml": ".svg",
    "image/x-icon": ".ico",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
}


@dataclass(frozen=True, slots=True)
class MirrorStats:
    posts: int
    topics: int
    users: int
    activities: int
    files: int


class MirrorIntegrityError(ValueError):
    """The candidate publication contains unresolved internal navigation/resources."""

    def __init__(
        self,
        message: str,
        *,
        missing_pages: set[str] | None = None,
        missing_resources: set[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.missing_pages = frozenset(missing_pages or ())
        self.missing_resources = frozenset(missing_resources or ())


@dataclass(frozen=True, slots=True)
class _PageCapture:
    capture_id: int
    sha256: str
    original_url: str
    timestamp: str
    relative_path: str
    route: PurePosixPath
    topic_id: int | None


@dataclass(frozen=True, slots=True)
class _PageAlias:
    capture_id: int
    original_url: str
    timestamp: str
    route: PurePosixPath


@dataclass(frozen=True, slots=True)
class _ResourceCapture:
    capture_id: int
    sha256: str
    canonical_url: str
    original_url: str
    timestamp: str
    relative_path: str
    mimetype: str | None
    target: PurePosixPath


def _resource_name(original_url: str, mimetype: str | None) -> str:
    basename = unquote(Path(urlsplit(original_url).path).name)
    safe = _SAFE_NAME_RE.sub("-", basename).strip(".-") or "recurso"
    expected_extension = _MIME_EXTENSIONS.get(mimetype or "")
    if not expected_extension:
        return safe[:160]
    stem = (
        safe[: -len(expected_extension)] if safe.casefold().endswith(expected_extension) else safe
    )
    return stem[: 160 - len(expected_extension)] + expected_extension


def _resource_target(sha256: str, original_url: str, mimetype: str | None) -> PurePosixPath:
    url_identity = (
        hashlib.sha256(canonical_url(original_url).encode()).hexdigest()[:16]
        if mimetype == "text/css"
        else "conteudo"
    )
    return PurePosixPath(
        "recursos",
        sha256[:2],
        sha256,
        url_identity,
        _resource_name(original_url, mimetype),
    )


def _route_source_url(original_url: str, actual_topic_id: int | None) -> str:
    if actual_topic_id is None:
        return original_url
    parts = urlsplit(original_url)
    if not parts.path.casefold().endswith("viewtopic.php"):
        return original_url
    query = parse_qsl(parts.query, keep_blank_values=True)
    if not any(key.casefold() == "view" and value in {"next", "previous"} for key, value in query):
        return original_url
    rewritten = [(key, value) for key, value in query if key.casefold() not in {"t", "view", "p"}]
    rewritten.append(("t", str(actual_topic_id)))
    return urlunsplit(parts._replace(query=urlencode(rewritten)))


def _is_intentionally_shared_route(route: PurePosixPath) -> bool:
    return route == PurePosixPath("busca", "index.html") or (
        len(route.parts) > 2 and route.parts[1] == "acoes"
    )


def _select_pages(
    database: ArchiveDB,
) -> tuple[list[_PageCapture], int, tuple[_PageAlias, ...]]:
    capture_topics: dict[int, int] = {}
    for row in database.connection.execute(
        """
        SELECT ps.capture_id, p.topic_id, count(*) AS occurrences
        FROM post_sources AS ps
        JOIN posts AS p ON p.post_pk=ps.post_pk
        WHERE p.topic_id IS NOT NULL
        GROUP BY ps.capture_id, p.topic_id
        ORDER BY ps.capture_id, occurrences DESC, p.topic_id
        """,
        (),
    ):
        capture_topics.setdefault(_row_int(row, "capture_id"), _row_int(row, "topic_id"))
    selected: dict[PurePosixPath, _PageCapture] = {}
    aliases: list[_PageAlias] = []
    duplicates = 0
    for row in database.connection.execute(
        """
        SELECT c.capture_id, c.original_url, c.requested_url, c.timestamp, c.raw_sha256,
               b.relative_path, b.byte_length, c.mimetype
        FROM captures AS c
        JOIN blobs AS b ON b.sha256=c.raw_sha256
        WHERE c.fetch_status=?
          AND coalesce(c.statuscode, ?)=?
          AND c.mimetype IN (?, ?)
        ORDER BY c.timestamp, c.capture_id
        """,
        ("fetched", 200, 200, "text/html", "application/xhtml+xml"),
    ):
        if not is_forum_url(str(row["original_url"]), str(row["timestamp"])):
            continue
        routed_url = _route_source_url(
            str(row["original_url"]), capture_topics.get(_row_int(row, "capture_id"))
        )
        route = static_route(routed_url, row["timestamp"])
        if route is None:
            continue
        aliases.append(
            _PageAlias(
                capture_id=_row_int(row, "capture_id"),
                original_url=str(row["original_url"]),
                timestamp=str(row["timestamp"]),
                route=route,
            )
        )
        requested_url = str(row["requested_url"]) if row["requested_url"] else None
        if requested_url and canonical_url(requested_url) != canonical_url(
            str(row["original_url"])
        ):
            aliases.append(
                _PageAlias(
                    capture_id=_row_int(row, "capture_id"),
                    original_url=requested_url,
                    timestamp=str(row["timestamp"]),
                    route=route,
                )
            )
        candidate = _PageCapture(
            capture_id=_row_int(row, "capture_id"),
            sha256=str(row["raw_sha256"]),
            original_url=str(row["original_url"]),
            timestamp=str(row["timestamp"]),
            relative_path=str(row["relative_path"]),
            route=route,
            topic_id=capture_topics.get(_row_int(row, "capture_id")),
        )
        previous = selected.get(route)
        if previous is not None:
            duplicates += 1
            if (
                previous.timestamp == candidate.timestamp
                and previous.sha256 != candidate.sha256
                and not _is_intentionally_shared_route(route)
            ):
                raise MirrorIntegrityError(
                    f"colisão incompatível na rota {route}: "
                    f"{previous.original_url} e {candidate.original_url}"
                )
        if previous is None or (
            candidate.timestamp,
            candidate.original_url,
            candidate.sha256,
        ) > (
            previous.timestamp,
            previous.original_url,
            previous.sha256,
        ):
            selected[route] = candidate
    return (
        sorted(selected.values(), key=lambda item: item.route.as_posix()),
        duplicates,
        tuple(aliases),
    )


def _load_resources(
    database: ArchiveDB,
) -> tuple[
    dict[str, list[_ResourceCapture]],
    dict[int, tuple[tuple[str, str], ...]],
    dict[str, _ResourceCapture],
]:
    candidates: dict[str, list[_ResourceCapture]] = defaultdict(list)
    unique_blobs: dict[str, _ResourceCapture] = {}
    for row in database.connection.execute(
        """
        SELECT c.capture_id, c.canonical_url, c.original_url, c.requested_url, c.timestamp,
               c.raw_sha256, b.relative_path, b.byte_length, c.mimetype
        FROM captures AS c
        JOIN blobs AS b ON b.sha256=c.raw_sha256
        WHERE c.fetch_status=?
          AND coalesce(c.statuscode, ?)=?
          AND c.mimetype NOT IN (?, ?)
          AND b.byte_length > 0
        ORDER BY c.canonical_url, c.timestamp, c.capture_id
        """,
        ("fetched", 200, 200, "text/html", "application/xhtml+xml"),
    ):
        sha256 = str(row["raw_sha256"])
        timestamp = str(row["timestamp"])
        mimetype = str(row["mimetype"]) if row["mimetype"] else None
        proposed_target = _resource_target(
            sha256,
            str(row["original_url"]),
            mimetype,
        )
        canonical = str(row["canonical_url"])
        identity = sha256
        if mimetype == "text/css":
            identity = f"{sha256}:{canonical}:{timestamp}"
            proposed_target = proposed_target.parent / timestamp / proposed_target.name
        unique = unique_blobs.setdefault(
            identity,
            _ResourceCapture(
                capture_id=_row_int(row, "capture_id"),
                sha256=sha256,
                canonical_url=canonical,
                original_url=str(row["original_url"]),
                timestamp=timestamp,
                relative_path=str(row["relative_path"]),
                mimetype=mimetype,
                target=proposed_target,
            ),
        )
        capture = _ResourceCapture(
            capture_id=_row_int(row, "capture_id"),
            sha256=sha256,
            canonical_url=canonical,
            original_url=str(row["original_url"]),
            timestamp=timestamp,
            relative_path=str(row["relative_path"]),
            mimetype=mimetype,
            target=unique.target,
        )
        candidates[canonical].append(capture)
        requested_url = str(row["requested_url"]) if row["requested_url"] else None
        if requested_url:
            requested_canonical = canonical_url(requested_url)
            if requested_canonical != canonical:
                candidates[requested_canonical].append(capture)
    for choices in candidates.values():
        choices.sort(key=lambda capture: (capture.timestamp, capture.capture_id, capture.sha256))
    references: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for row in database.connection.execute(
        """
        SELECT referrer_capture_id, target_url, kind
        FROM resource_references
        WHERE kind IN (?, ?, ?)
        ORDER BY referrer_capture_id, target_url
        """,
        ("page", "asset", "attachment"),
    ):
        references[_row_int(row, "referrer_capture_id")].append(
            (str(row["target_url"]), str(row["kind"]))
        )
    return candidates, {key: tuple(value) for key, value in references.items()}, unique_blobs


def _timestamp_seconds(value: str) -> int:
    try:
        parsed = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        return int(parsed.timestamp())
    except (OverflowError, TypeError, ValueError) as error:
        raise MirrorIntegrityError(f"timestamp inválido: {value}") from error


def _nearest_candidate(
    choices: list[_ResourceCapture],
    timestamp_value: str,
) -> _ResourceCapture:
    index = bisect.bisect_left(
        choices,
        timestamp_value,
        key=lambda candidate: candidate.timestamp,
    )
    nearby = choices[max(0, index - 1) : min(len(choices), index + 1)]
    timestamp = _timestamp_seconds(timestamp_value)
    return min(
        nearby,
        key=lambda candidate: (
            abs(_timestamp_seconds(candidate.timestamp) - timestamp),
            0 if candidate.timestamp <= timestamp_value else 1,
            candidate.timestamp,
            candidate.original_url,
            candidate.sha256,
        ),
    )


@dataclass(frozen=True, slots=True)
class _ResolvedGraph:
    page_resources: dict[int, dict[str, PurePosixPath]]
    resource_resources: dict[int, dict[str, PurePosixPath]]
    resources: dict[PurePosixPath, _ResourceCapture]
    missing_pages: set[str]
    missing_resources: set[str]


def _resolve_complete_graph(
    pages: list[_PageCapture],
    registry: RouteRegistry,
    references: dict[int, tuple[tuple[str, str], ...]],
    candidates: dict[str, list[_ResourceCapture]],
) -> _ResolvedGraph:
    page_resources: dict[int, dict[str, PurePosixPath]] = {}
    resource_resources: dict[int, dict[str, PurePosixPath]] = {}
    selected_resources: dict[PurePosixPath, _ResourceCapture] = {}
    queued_capture_ids: set[int] = set()
    queue: list[_ResourceCapture] = []
    missing_pages: set[str] = set()
    missing_resources: set[str] = set()
    page_resolution_cache: dict[tuple[str, str | None], bool] = {}

    def resolve_resources(
        capture_id: int,
        timestamp: str,
        captured_references: tuple[tuple[str, str], ...],
        destination: dict[int, dict[str, PurePosixPath]],
    ) -> None:
        resolved: dict[str, PurePosixPath] = {}
        for target_url, kind in captured_references:
            canonical = canonical_url(target_url)
            if kind == "page":
                cache_key = (canonical, era_for_url(target_url, timestamp))
                try:
                    resolved_page = page_resolution_cache[cache_key]
                except KeyError:
                    resolved_page = registry.resolve(target_url, timestamp) is not None
                    page_resolution_cache[cache_key] = resolved_page
                if not resolved_page and not is_inert_action_url(target_url):
                    missing_pages.add(target_url)
                continue
            choices = candidates.get(canonical)
            if not choices:
                missing_resources.add(target_url)
                continue
            candidate = _nearest_candidate(choices, timestamp)
            resolved[canonical] = candidate.target
            selected_resources.setdefault(candidate.target, candidate)
            if candidate.mimetype == "text/css" and candidate.capture_id not in queued_capture_ids:
                queued_capture_ids.add(candidate.capture_id)
                queue.append(candidate)
        destination[capture_id] = resolved

    for page in pages:
        resolve_resources(
            page.capture_id,
            page.timestamp,
            references.get(page.capture_id, ()),
            page_resources,
        )
    cursor = 0
    while cursor < len(queue):
        resource = queue[cursor]
        cursor += 1
        resolve_resources(
            resource.capture_id,
            resource.timestamp,
            references.get(resource.capture_id, ()),
            resource_resources,
        )

    return _ResolvedGraph(
        page_resources=page_resources,
        resource_resources=resource_resources,
        resources=selected_resources,
        missing_pages=missing_pages,
        missing_resources=missing_resources,
    )


def _copy_resources(
    archive_root: Path,
    staging: Path,
    resources: dict[PurePosixPath, _ResourceCapture],
    resource_resources: dict[int, dict[str, PurePosixPath]],
) -> int:
    copied = 0
    for resource in resources.values():
        source = archive_root / resource.relative_path
        target = staging / resource.target
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            continue
        if resource.mimetype == "text/css":
            resolved = resource_resources.get(resource.capture_id, {})
            target.write_text(
                preserve_stylesheet(
                    source.read_bytes(),
                    resource.original_url,
                    resource.target,
                    resolved,
                ),
                encoding="utf-8",
                newline="\n",
            )
        elif resource.mimetype == "image/svg+xml":
            target.write_text(
                preserve_svg(source.read_bytes()),
                encoding="utf-8",
                newline="\n",
            )
        else:
            shutil.copyfile(source, target)
        copied += 1
    return copied


def _local_target(root: Path, source: Path, reference: str) -> tuple[Path, str]:
    parts = urlsplit(reference)
    if parts.scheme or parts.netloc:
        raise ValueError("referência externa")
    decoded = unquote(parts.path)
    if decoded.startswith("/"):
        decoded = decoded.removeprefix("/unidev/").lstrip("/")
        target = root / decoded
    else:
        target = source.parent / decoded if decoded else source
    target = target.resolve()
    if not target.is_relative_to(root.resolve()):
        raise MirrorIntegrityError(f"referência escapa do site: {source}: {reference}")
    if target.is_dir():
        target /= "index.html"
    return target, parts.fragment


def _validate_output(staging: Path) -> None:
    identifiers: dict[Path, set[str]] = {}

    def target_identifiers(path: Path) -> set[str]:
        cached = identifiers.get(path)
        if cached is not None:
            return cached
        document = lxml_html.document_fromstring(path.read_bytes())
        found = {str(value) for value in document.xpath("//*[@id]/@id | //a[@name]/@name")}
        identifiers[path] = found
        return found

    for source in staging.rglob("*.html"):
        value = source.read_text(encoding="utf-8")
        document = lxml_html.document_fromstring(value)
        changed = False
        for css_value in document.xpath("//@style | //style/text()"):
            css_text = str(css_value)
            if 'url("")' in css_text or "url('')" in css_text:
                raise MirrorIntegrityError(f"recurso inline ausente em {source}")
            if has_unsupported_network_syntax(css_text):
                raise MirrorIntegrityError(f"sintaxe CSS de rede não suportada em {source}")
        for element in document.xpath("//*[@srcset]"):
            for candidate in parse_srcset(element.get("srcset", "")):
                reference = candidate.url
                if not reference or reference.startswith("data:"):
                    continue
                parts = urlsplit(reference)
                if parts.scheme or parts.netloc:
                    raise MirrorIntegrityError(f"candidato srcset externo em {source}: {reference}")
                target, _ = _local_target(staging, source, reference)
                if not target.is_file():
                    raise MirrorIntegrityError(
                        f"candidato srcset quebrado em {source}: {reference}"
                    )
        for element in document.xpath("//*[@href or @src or @background or @poster or @action]"):
            for attribute in ("href", "src", "background", "poster", "action"):
                reference = element.get(attribute)
                if not reference or reference.startswith("data:"):
                    continue
                parts = urlsplit(reference)
                if parts.scheme or parts.netloc:
                    if element.tag.casefold() in {"a", "area"}:
                        raise MirrorIntegrityError(f"link externo ativo em {source}: {reference}")
                    raise MirrorIntegrityError(f"subrecurso externo em {source}: {reference}")
                target, fragment = _local_target(staging, source, reference)
                if not target.is_file():
                    raise MirrorIntegrityError(f"link local quebrado em {source}: {reference}")
                if (
                    fragment
                    and target.suffix.casefold() in {".html", ".htm"}
                    and fragment not in target_identifiers(target)
                ):
                    without_fragment = urlunsplit(
                        (parts.scheme, parts.netloc, parts.path, parts.query, "")
                    )
                    if without_fragment:
                        element.set(attribute, without_fragment)
                    else:
                        element.attrib.pop(attribute, None)
                        classes = element.get("class", "").split()
                        if "archive-link-missing" not in classes:
                            classes.append("archive-link-missing")
                        element.set("class", " ".join(classes))
                        element.set("title", "Âncora local não preservada")
                        element.set("aria-disabled", "true")
                    changed = True
        if changed:
            source.write_text(
                "<!doctype html>\n"
                + cast(str, lxml_html.tostring(document, encoding="unicode", method="html")),
                encoding="utf-8",
                newline="\n",
            )
    for source in staging.rglob("*.css"):
        value = source.read_text(encoding="utf-8")
        generated_pagefind = source.is_relative_to(staging / "pagefind")
        if not generated_pagefind and has_unsupported_network_syntax(value):
            raise MirrorIntegrityError(f"recurso CSS inseguro em {source}")
        for candidate in css_references(value):
            reference = candidate.value
            if not reference:
                raise MirrorIntegrityError(f"recurso CSS ausente em {source}")
            if reference.startswith("data:"):
                continue
            try:
                target, _ = _local_target(staging, source, reference)
            except ValueError as error:
                raise MirrorIntegrityError(
                    f"subrecurso CSS externo em {source}: {reference}"
                ) from error
            if not target.is_file():
                raise MirrorIntegrityError(f"subrecurso CSS quebrado em {source}: {reference}")
    for source in staging.rglob("*.svg"):
        document = lxml_html.fromstring(source.read_bytes())
        for element in document.iter():
            tag = local_name(element.tag)
            if tag in {
                "script",
                "foreignobject",
                "iframe",
                "object",
                "embed",
                "link",
                "audio",
                "video",
                "animate",
                "animatemotion",
                "animatetransform",
                "set",
                "discard",
                "style",
            }:
                raise MirrorIntegrityError(f"SVG ativo em {source}: {tag}")
            for attribute, value in element.attrib.items():
                lowered = local_name(attribute)
                if (
                    lowered.startswith("on")
                    or lowered == "style"
                    or (lowered == "href" and value and not value.startswith("#"))
                    or "javascript:" in value.casefold()
                    or "url(" in value.casefold()
                ):
                    raise MirrorIntegrityError(f"atributo SVG ativo em {source}: {attribute}")


def _enable_search_page(value: str, output_file: PurePosixPath) -> str:
    document = lxml_html.document_fromstring(value)
    body_nodes = document.xpath("//body")
    if not body_nodes:
        raise MirrorIntegrityError("captura de busca sem body")
    body = body_nodes[0]
    panel = lxml_html.fragment_fromstring(
        """<section id="archive-search-panel" data-pagefind-ignore="all">
<h1>Busca geral no acervo UniDev</h1>
<form id="search-form" method="get" role="search">
<label for="search-input">Pesquise tópicos, mensagens, trechos de código ou usuários</label>
<input id="search-input" name="q" type="search" autocomplete="off" required placeholder="Digite o que deseja encontrar">
<button type="submit">Buscar</button>
</form>
<p id="search-status" aria-live="polite"></p>
<ol id="search-results"></ol>
</section>"""
    )
    body.insert(0, panel)
    script = body.makeelement("script")
    script.set("type", "module")
    script.set("src", posixpath.relpath("assets/search.js", output_file.parent.as_posix()))
    body.append(script)
    csp_nodes = document.xpath(
        '//meta[translate(@http-equiv, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", '
        '"abcdefghijklmnopqrstuvwxyz")="content-security-policy"]'
    )
    if not csp_nodes:
        raise MirrorIntegrityError("captura de busca sem CSP")
    csp_nodes[0].set(
        "content",
        csp_nodes[0].get("content", "")
        + "; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'",
    )
    serialized = lxml_html.tostring(document, encoding="unicode", method="html")
    if isinstance(serialized, bytes):
        serialized = serialized.decode("utf-8")
    return "<!doctype html>\n" + str(serialized)


def _homepage_page(pages: list[_PageCapture]) -> _PageCapture:
    candidates = [
        page
        for page in pages
        if urlsplit(page.original_url)
        .path.casefold()
        .endswith(("/portal.php", "/index.php", "/default.aspx", "/forum/"))
    ]
    if not candidates:
        raise MirrorIntegrityError("publicação bloqueada: índice histórico não capturado")
    return max(
        candidates,
        key=lambda page: (
            urlsplit(page.original_url).path.casefold().endswith("/portal.php"),
            page.timestamp,
            page.capture_id,
        ),
    )


def _copy_search_assets(staging: Path) -> None:
    source = Path(__file__).with_name("static")
    target = staging / "assets"
    target.mkdir(parents=True, exist_ok=True)
    for name in (
        "archive-entities.css",
        "archive-search.css",
        "search.js",
        "site.css",
        "img-not-found.svg",
    ):
        shutil.copyfile(source / name, target / name)
    policy = staging / "politica" / "index.html"
    policy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / "privacy-policy.html", policy)
    not_found = staging / "404.html"
    shutil.copyfile(source / "404.html", not_found)


def build_mirror_site(
    database: ArchiveDB,
    archive_root: str | Path,
    output: str | Path,
    *,
    period_start: str | None = None,
    period_end: str | None = None,
) -> MirrorStats:
    """Build captured pages and transparent navigation from verified records."""

    archive = Path(archive_root)
    destination = Path(output)
    staging = destination.with_name(destination.name + ".staging")
    _remove_tree(staging)
    staging.mkdir(parents=True)

    pages, duplicates, page_aliases = _select_pages(database)
    homepage = _homepage_page(pages)
    search_available = any(page.route == PurePosixPath("busca", "index.html") for page in pages)
    aliases_by_capture: dict[int, _PageAlias] = {}
    for alias in page_aliases:
        aliases_by_capture.setdefault(alias.capture_id, alias)

    def mapped_entries() -> Iterable[tuple[str, str, PurePosixPath]]:
        return chain(
            ((alias.original_url, alias.timestamp, alias.route) for alias in page_aliases),
            (
                (
                    homepage.original_url,
                    homepage.timestamp,
                    PurePosixPath("index.html"),
                ),
            ),
        )

    def post_entries() -> Iterable[tuple[str, str, PurePosixPath, int]]:
        post_rows = database.connection.execute(
            """
            SELECT ps.capture_id, p.historical_id
            FROM post_sources AS ps
            JOIN posts AS p ON p.post_pk=ps.post_pk
            WHERE p.historical_id IS NOT NULL
            ORDER BY ps.capture_id, p.historical_id
            """,
            (),
        )
        return (
            (
                alias.original_url,
                alias.timestamp,
                alias.route,
                _row_int(row, "historical_id"),
            )
            for row in post_rows
            if (alias := aliases_by_capture.get(_row_int(row, "capture_id"))) is not None
        )

    captured_registry = RouteRegistry.from_mapped_entries(mapped_entries(), post_entries())
    resource_candidates, references, _ = _load_resources(database)
    entity_plan = plan_entity_fallbacks(
        database,
        captured_registry,
        references,
        {page.capture_id: page.timestamp for page in pages},
    )
    registry = RouteRegistry.from_mapped_entries(
        chain(mapped_entries(), entity_plan.aliases),
        post_entries(),
    )
    graph = _resolve_complete_graph(pages, registry, references, resource_candidates)
    _copy_resources(
        archive,
        staging,
        graph.resources,
        graph.resource_resources,
    )

    for page in pages:
        raw = (archive / page.relative_path).read_bytes()
        resources = graph.page_resources.get(page.capture_id, {})
        preserved = preserve_document(
            raw,
            page.original_url,
            page.route,
            registry,
            resources,
            capture_timestamp=page.timestamp,
            period_start=period_start,
            period_end=period_end,
            general_search_available=search_available,
        )
        if page.route == PurePosixPath("busca", "index.html"):
            preserved = _enable_search_page(preserved, page.route)
        target = staging / page.route
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(preserved, encoding="utf-8", newline="\n")

    raw = (archive / homepage.relative_path).read_bytes()
    resources = graph.page_resources.get(homepage.capture_id, {})
    (staging / "index.html").write_text(
        preserve_document(
            raw,
            homepage.original_url,
            PurePosixPath("index.html"),
            registry,
            resources,
            capture_timestamp=homepage.timestamp,
            period_start=period_start,
            period_end=period_end,
            general_search_available=search_available,
        ),
        encoding="utf-8",
        newline="\n",
    )

    source_routes_by_capture = {alias.capture_id: alias.route for alias in page_aliases}
    topic_source_routes_mutable: dict[tuple[str, int], set[PurePosixPath]] = defaultdict(set)
    for page in pages:
        era = era_for_url(page.original_url, page.timestamp)
        if era is not None and page.topic_id is not None:
            topic_source_routes_mutable[(era, page.topic_id)].add(page.route)
    topic_source_routes = {
        key: tuple(sorted(routes)) for key, routes in topic_source_routes_mutable.items()
    }
    generated_entities = write_entity_pages(
        database,
        staging,
        entity_plan.entities,
        source_routes_by_capture,
        topic_source_routes,
    )
    _copy_search_assets(staging)
    (staging / ".nojekyll").write_text("", encoding="utf-8")
    counts = database.counts()
    stats = MirrorStats(
        posts=counts.get("posts", 0),
        topics=counts.get("topics", 0),
        users=counts.get("users", 0),
        activities=counts.get("activities", 0),
        files=0,
    )
    report = {
        **asdict(stats),
        "captured_pages": len(pages),
        "discarded_duplicate_routes": duplicates,
        "copied_resource_blobs": len(graph.resources),
        "generated_entity_pages": generated_entities,
        "restored_entity_links": entity_plan.resolved_urls,
        "neutralized_uncaptured_page_links": len(graph.missing_pages),
        "neutralized_uncaptured_resources": len(graph.missing_resources),
        "encoding": "UTF-8",
        "runtime_backend": None,
    }
    (staging / "build-manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _validate_output(staging)
    _remove_tree(destination)
    staging.replace(destination)
    return MirrorStats(
        posts=stats.posts,
        topics=stats.topics,
        users=stats.users,
        activities=stats.activities,
        files=sum(1 for path in destination.rglob("*") if path.is_file()),
    )
