"""Collision-safe mapping from historical dynamic URLs to static GitHub Pages files."""

from __future__ import annotations

import bisect
import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from unidev_archive.urls import canonical_url, era_for_url

_SAFE_SEGMENT_RE = re.compile(r"[^a-z0-9_-]+")
_COMMUNITY_THREAD_PATH_RE = re.compile(
    r"/forums/(?:thread/(\d+)\.aspx|permalink/(\d+)/\d+/showthread\.aspx|(\d+)/showthread\.aspx)$",
    re.I,
)
_COMMUNITY_FORUM_PATH_RE = re.compile(r"/forums/(\d+)/showforum\.aspx$", re.I)
_IGNORED_KEYS = {"sid", "phpbb3_sid", "highlight", "hilit"}
_PRESENTATION_DEFAULTS = {("postdays", "0"), ("postorder", "asc"), ("topicdays", "0")}


def _timestamp_seconds(value: str) -> int:
    return int(datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC).timestamp())


@dataclass(frozen=True, slots=True)
class LinkResolution:
    path: PurePosixPath
    fragment: str


def _query(url: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        lowered = key.casefold()
        if (
            lowered not in _IGNORED_KEYS
            and (lowered, value.casefold()) not in _PRESENTATION_DEFAULTS
        ):
            values[lowered] = value
    return values


def _number(query: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = query.get(key)
        if value and value.isdigit():
            return str(int(value))
    return None


def _offset_route(prefix: tuple[str, ...], value: str | None, label: str) -> PurePosixPath:
    if value is None or value == "0":
        return PurePosixPath(*prefix, "index.html")
    return PurePosixPath(*prefix, label, value, "index.html")


def _with_variant(
    route: PurePosixPath,
    url: str,
    query: dict[str, str],
    allowed_keys: set[str],
) -> PurePosixPath:
    parent = route.parent
    changed = False
    if post_id := _number(query, "p"):
        parent = parent / "post" / post_id
        changed = True
    if representation := query.get("view", "").casefold():
        safe_representation = _SAFE_SEGMENT_RE.sub("-", representation).strip("-")
        parent = parent / "visualizacao" / (safe_representation or "desconhecida")
        changed = True
    unknown_keys = set(query).difference(allowed_keys, {"p", "view"})
    if unknown_keys:
        suffix = hashlib.blake2s(
            canonical_url(url).encode("utf-8"),
            digest_size=8,
        ).hexdigest()
        parent = parent / "variante" / suffix
        changed = True
    return parent / "index.html" if changed else route


def is_inert_action_url(url: str) -> bool:
    parts = urlsplit(url)
    endpoint = parts.path.rsplit("/", 1)[-1].casefold()
    query = _query(url)
    if endpoint in {
        "addpost.aspx",
        "createuser.aspx",
        "post.asp",
        "reply.asp",
        "posting.php",
        "login.asp",
        "login.aspx",
        "login.php",
        "privmsg.php",
        "register.asp",
        "threadnavigation.aspx",
    }:
        return True
    if endpoint == "ucp.php":
        return True
    if endpoint == "viewforum.php" and query.get("mark") == "topics":
        return True
    return endpoint == "profile.php" and query.get("mode") in {"editprofile", "register"}


def _unknown_route(era: str, endpoint: str, url: str) -> PurePosixPath:
    name = _SAFE_SEGMENT_RE.sub("-", endpoint.casefold()).strip("-") or "index"
    identity = canonical_url(url)
    if not urlsplit(identity).query:
        return PurePosixPath(era, "paginas", name, "index.html")
    suffix = hashlib.blake2s(identity.encode("utf-8"), digest_size=8).hexdigest()
    return PurePosixPath(era, "paginas", name, suffix, "index.html")


def static_route(url: str, timestamp: str | None = None) -> PurePosixPath | None:
    """Map an actual historical forum page URL to one deterministic output file."""

    era = era_for_url(url, timestamp)
    if era is None:
        return None
    namespace = {
        "snitz": "forum",
        "phpbb2": "phpbb2",
        "phpbb3": "phpbb3",
        "community-server": "comunidade",
    }[era]
    parts = urlsplit(url)
    endpoint = (
        "index" if parts.path.endswith("/") else parts.path.rsplit("/", 1)[-1].casefold() or "index"
    )
    query = _query(url)

    if endpoint in {"search.asp", "search.php", "searchresults.aspx"}:
        return PurePosixPath("busca", "index.html")
    if is_inert_action_url(url):
        category = (
            "login"
            if endpoint
            in {
                "createuser.aspx",
                "login.asp",
                "login.aspx",
                "login.php",
                "privmsg.php",
                "register.asp",
                "ucp.php",
            }
            or query.get("mode") in {"editprofile", "register"}
            else "somente-leitura"
        )
        return PurePosixPath(namespace, "acoes", category, "index.html")

    if era == "community-server":
        if match := _COMMUNITY_THREAD_PATH_RE.search(parts.path):
            thread_id = match.group(1) or match.group(2) or match.group(3)
            if thread_id is not None:
                return PurePosixPath(namespace, "topicos", thread_id, "index.html")
        if match := _COMMUNITY_FORUM_PATH_RE.search(parts.path):
            return PurePosixPath(namespace, "foruns", match.group(1), "index.html")

    if era == "snitz":
        if endpoint == "topic.asp" and (topic_id := _number(query, "topic_id")):
            page = _number(query, "whichpage")
            return _offset_route(
                (namespace, "topicos", topic_id),
                None if page == "1" else page,
                "pagina",
            )
        if endpoint == "forum.asp" and (forum_id := _number(query, "forum_id")):
            page = _number(query, "whichpage")
            return _offset_route(
                (namespace, "foruns", forum_id),
                None if page == "1" else page,
                "pagina",
            )
        if endpoint == "pop_profile.asp" and (user_id := _number(query, "id")):
            return PurePosixPath(namespace, "usuarios", user_id, "index.html")

    if era in {"phpbb2", "phpbb3"}:
        if endpoint == "viewtopic.php":
            if topic_id := _number(query, "t"):
                route = _offset_route(
                    (namespace, "topicos", topic_id),
                    _number(query, "start"),
                    "inicio",
                )
                return _with_variant(
                    route,
                    url,
                    query,
                    {"f", "t", "start"},
                )
            if post_id := _number(query, "p"):
                return PurePosixPath(namespace, "posts", post_id, "index.html")
        if endpoint == "viewforum.php" and (forum_id := _number(query, "f")):
            route = _offset_route(
                (namespace, "foruns", forum_id),
                _number(query, "start"),
                "inicio",
            )
            return _with_variant(route, url, query, {"f", "start"})
        if (
            endpoint in {"memberlist.php", "profile.php"}
            and query.get("mode", "").casefold() == "viewprofile"
            and (user_id := _number(query, "u", "id"))
        ):
            return PurePosixPath(namespace, "usuarios", user_id, "index.html")

    if endpoint in {"index", "index.php", "default.aspx"} and not query:
        return PurePosixPath(namespace, "index.html")
    return _unknown_route(namespace, endpoint.rsplit(".", 1)[0], url)


class RouteRegistry:
    """Resolve links only when their mapped output is backed by a real capture."""

    __slots__ = ("_aliases", "_paths", "_post_aliases")

    def __init__(
        self,
        paths: set[PurePosixPath],
        aliases: dict[str, tuple[tuple[int, PurePosixPath], ...]] | None = None,
        post_aliases: dict[tuple[str, str], tuple[tuple[int, PurePosixPath], ...]] | None = None,
    ) -> None:
        self._paths = frozenset(paths)
        self._aliases = aliases or {}
        self._post_aliases = post_aliases or {}

    @classmethod
    def from_urls(
        cls,
        urls: tuple[str, ...] | list[str],
        timestamp: str | None = None,
    ) -> RouteRegistry:
        return cls.from_entries((url, timestamp) for url in urls)

    @classmethod
    def from_entries(
        cls,
        entries: Iterable[tuple[str, str | None]],
    ) -> RouteRegistry:
        return cls(
            {
                route
                for url, timestamp in entries
                if (route := static_route(url, timestamp)) is not None
            }
        )

    @classmethod
    def from_mapped_entries(
        cls,
        entries: Iterable[tuple[str, str, PurePosixPath]],
        post_entries: Iterable[tuple[str, str, PurePosixPath, int]] = (),
    ) -> RouteRegistry:
        paths: set[PurePosixPath] = set()
        mutable_aliases: dict[str, list[tuple[int, PurePosixPath]]] = {}
        for url, timestamp, path in entries:
            paths.add(path)
            seconds = _timestamp_seconds(timestamp)
            if static_route(url, timestamp) != path:
                mutable_aliases.setdefault(canonical_url(url), []).append((seconds, path))
        aliases = {url: tuple(sorted(candidates)) for url, candidates in mutable_aliases.items()}
        mutable_posts: dict[tuple[str, str], list[tuple[int, PurePosixPath]]] = {}
        for url, timestamp, path, post_id in post_entries:
            if (era := era_for_url(url, timestamp)) is not None:
                mutable_posts.setdefault((era, str(post_id)), []).append(
                    (_timestamp_seconds(timestamp), path)
                )
        post_aliases = {key: tuple(sorted(candidates)) for key, candidates in mutable_posts.items()}
        return cls(paths, aliases, post_aliases)

    @staticmethod
    def _nearest_path(
        candidates: tuple[tuple[int, PurePosixPath], ...],
        timestamp: str | None,
    ) -> PurePosixPath:
        if timestamp is None:
            return candidates[-1][1]
        target_seconds = _timestamp_seconds(timestamp)
        offset = bisect.bisect_left(candidates, target_seconds, key=lambda candidate: candidate[0])
        return min(
            candidates[max(0, offset - 1) : offset + 1],
            key=lambda candidate: (
                abs(candidate[0] - target_seconds),
                candidate[0],
            ),
        )[1]

    def resolve(self, url: str, timestamp: str | None = None) -> LinkResolution | None:
        fragment = urlsplit(url).fragment
        path = static_route(url, timestamp)
        if path is not None and path in self._paths:
            return LinkResolution(path=path, fragment=fragment)
        candidates = self._aliases.get(canonical_url(url))
        if candidates:
            return LinkResolution(path=self._nearest_path(candidates, timestamp), fragment=fragment)
        query = _query(url)
        if query.get("view", "").casefold() == "print":
            parts = urlsplit(url)
            normal_url = urlunsplit(
                parts._replace(
                    query=urlencode(
                        [
                            (key, value)
                            for key, value in parse_qsl(parts.query, keep_blank_values=True)
                            if key.casefold() != "view"
                        ]
                    )
                )
            )
            normal_path = static_route(normal_url, timestamp)
            if normal_path is not None and normal_path in self._paths:
                return LinkResolution(path=normal_path, fragment=fragment)
        post_id = _number(query, "p")
        era = era_for_url(url, timestamp)
        if era is not None and post_id is not None:
            candidates = self._post_aliases.get((era, post_id))
            if candidates:
                return LinkResolution(
                    path=self._nearest_path(candidates, timestamp), fragment=fragment
                )
        return None

    def __contains__(self, path: PurePosixPath) -> bool:
        return path in self._paths
