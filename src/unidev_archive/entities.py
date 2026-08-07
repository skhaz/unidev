from __future__ import annotations

import html
import posixpath
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Protocol
from urllib.parse import quote

from .database import ArchiveDB
from .routing import RouteRegistry, static_route

_NAMESPACE_ERAS = {
    "forum": "snitz",
    "comunidade": "community-server",
    "phpbb2": "phpbb2",
    "phpbb3": "phpbb3",
}
_ERA_NAMESPACES = {era: namespace for namespace, era in _NAMESPACE_ERAS.items()}
_ENTITY_KINDS = frozenset({"foruns", "topicos", "usuarios"})
_CONTROL_TRANSLATION = {value: None for value in (*range(9), 11, 12, *range(14, 32), 127)}


class _Row(Protocol):
    def __getitem__(self, key: int | str) -> object: ...


def _row_int(row: _Row, key: int | str) -> int:
    try:
        return int(str(row[key]))
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f"valor inteiro inválido na coluna {key}") from error


@dataclass(frozen=True, slots=True, order=True)
class EntityRoute:
    route: PurePosixPath
    era: str
    kind: str
    historical_id: int


@dataclass(frozen=True, slots=True)
class EntityFallbackPlan:
    aliases: tuple[tuple[str, str, PurePosixPath], ...]
    entities: tuple[EntityRoute, ...]
    resolved_urls: int


def _entity_route(era: str, kind: str, historical_id: int) -> EntityRoute:
    namespace = _ERA_NAMESPACES[era]
    route = PurePosixPath("acervo", namespace, kind, str(historical_id), "index.html")
    return EntityRoute(route, era, kind, historical_id)


def _route_entity(url: str, timestamp: str) -> tuple[str, str, int] | None:
    route = static_route(url, timestamp)
    if route is None:
        return None
    parts = route.parts
    if len(parts) < 3 or parts[0] not in _NAMESPACE_ERAS or parts[1] not in _ENTITY_KINDS:
        return None
    try:
        historical_id = int(parts[2])
    except ValueError:
        return None
    return _NAMESPACE_ERAS[parts[0]], parts[1], historical_id


def plan_entity_fallbacks(
    database: ArchiveDB,
    registry: RouteRegistry,
    references: Mapping[int, tuple[tuple[str, str], ...]],
    capture_timestamps: Mapping[int, str],
) -> EntityFallbackPlan:
    existing = {
        "topicos": {
            (str(row["era"]), _row_int(row, "topic_id"))
            for row in database.connection.execute("SELECT era, topic_id FROM topics")
        },
        "usuarios": {
            (str(row["era"]), _row_int(row, "historical_id"))
            for row in database.connection.execute(
                "SELECT era, historical_id FROM users WHERE historical_id IS NOT NULL"
            )
        },
        "foruns": {
            (str(row["era"]), _row_int(row, "forum_id"))
            for row in database.connection.execute("SELECT era, forum_id FROM forums")
        },
    }
    aliases: set[tuple[str, str, PurePosixPath]] = set()
    entities: set[EntityRoute] = set()
    resolved_urls: set[str] = set()
    # Todas as entidades conhecidas do banco ganham página consolidada,
    # para o fórum ser integralmente navegável, mesmo sem captura.
    for row in database.connection.execute("SELECT era, topic_id FROM topics"):
        entities.add(_entity_route(str(row["era"]), "topicos", _row_int(row, "topic_id")))
    for row in database.connection.execute("SELECT era, forum_id FROM forums"):
        entities.add(_entity_route(str(row["era"]), "foruns", _row_int(row, "forum_id")))
    for row in database.connection.execute(
        "SELECT era, historical_id FROM users WHERE historical_id IS NOT NULL"
    ):
        entities.add(_entity_route(str(row["era"]), "usuarios", _row_int(row, "historical_id")))
    for capture_id, captured_references in references.items():
        timestamp = capture_timestamps.get(capture_id)
        if timestamp is None:
            continue
        for target_url, kind in captured_references:
            if kind != "page" or registry.resolve(target_url, timestamp) is not None:
                continue
            identity = _route_entity(target_url, timestamp)
            if identity is None:
                continue
            era, entity_kind, historical_id = identity
            if (era, historical_id) not in existing[entity_kind]:
                continue
            entity = _entity_route(era, entity_kind, historical_id)
            aliases.add((target_url, timestamp, entity.route))
            entities.add(entity)
            resolved_urls.add(target_url)

    selected_forums = {
        (entity.era, entity.historical_id) for entity in entities if entity.kind == "foruns"
    }
    selected_users = {
        (entity.era, entity.historical_id) for entity in entities if entity.kind == "usuarios"
    }
    user_keys = {
        (str(row["era"]), _row_int(row, "historical_id")): _row_int(row, "user_pk")
        for row in database.connection.execute(
            "SELECT user_pk, era, historical_id FROM users WHERE historical_id IS NOT NULL"
        )
    }
    selected_user_pks = {user_keys[key] for key in selected_users if key in user_keys}
    for row in database.connection.execute(
        "SELECT era, topic_id, forum_id FROM topics WHERE forum_id IS NOT NULL"
    ):
        if (str(row["era"]), _row_int(row, "forum_id")) in selected_forums:
            entities.add(_entity_route(str(row["era"]), "topicos", _row_int(row, "topic_id")))
    for row in database.connection.execute(
        "SELECT DISTINCT era, topic_id, user_pk FROM posts WHERE topic_id IS NOT NULL"
    ):
        if row["user_pk"] is not None and _row_int(row, "user_pk") in selected_user_pks:
            entities.add(_entity_route(str(row["era"]), "topicos", _row_int(row, "topic_id")))
    return EntityFallbackPlan(
        aliases=tuple(sorted(aliases, key=lambda item: (item[0], item[1], item[2].as_posix()))),
        entities=tuple(sorted(entities)),
        resolved_urls=len(resolved_urls),
    )


def _clean(value: object | None) -> str:
    return html.escape(str(value or "").translate(_CONTROL_TRANSLATION), quote=True)


def _href(source: PurePosixPath, target: PurePosixPath, fragment: str | None = None) -> str:
    relative = posixpath.relpath(target.as_posix(), source.parent.as_posix())
    suffix = f"#{quote(fragment, safe='')}" if fragment else ""
    return quote(relative, safe="/") + suffix


def _begin_page(
    handle: IO[str],
    route: PurePosixPath,
    title: str,
    heading: str,
    details: tuple[tuple[str, object | None], ...],
    *,
    search_available: bool,
) -> None:
    stylesheet = _href(route, PurePosixPath("assets", "archive-entities.css"))
    home = _href(route, PurePosixPath("index.html"))
    search_link = ""
    if search_available:
        search = _href(route, PurePosixPath("busca", "index.html"))
        search_link = f'<a href="{search}">Busca geral</a>'
    handle.write(
        '<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,follow">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'\">"
        '<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' '
        "viewBox='0 0 32 32'%3E%3Cpath fill='%238d1717' d='M3 3h26v26H3z'/%3E%3C/svg%3E\">"
        f"<title>{_clean(title)} — Acervo UniDev</title>"
        f'<link rel="stylesheet" href="{stylesheet}"></head><body>'
        f'<header class="entity-header"><a href="{home}">UniDev</a>{search_link}</header><main>'
        '<aside class="entity-notice"><strong>Visão consolidada do acervo.</strong> '
        "Esta página foi gerada somente com registros verificáveis extraídos das capturas; "
        "não é uma página histórica original e pode estar incompleta.</aside>"
        f'<h1>{_clean(heading)}</h1><dl class="entity-details">'
    )
    for label, value in details:
        handle.write(f"<dt>{_clean(label)}</dt><dd>{_clean(value) or '—'}</dd>")
    handle.write("</dl>")


def _end_page(handle: IO[str]) -> None:
    handle.write("</main></body></html>")
    handle.close()


def _open_page(root: Path, entity: EntityRoute) -> IO[str]:
    target = root / entity.route
    target.parent.mkdir(parents=True, exist_ok=True)
    return target.open("w", encoding="utf-8", newline="\n")


def write_entity_pages(
    database: ArchiveDB,
    output: Path,
    entities: tuple[EntityRoute, ...],
    source_routes_by_capture: Mapping[int, PurePosixPath],
    topic_source_routes: Mapping[tuple[str, int], tuple[PurePosixPath, ...]],
    *,
    search_available: bool,
) -> int:
    entity_by_key = {(entity.kind, entity.era, entity.historical_id): entity for entity in entities}
    topic_entities = {
        (entity.era, entity.historical_id): entity
        for entity in entities
        if entity.kind == "topicos"
    }
    user_entities = {
        (entity.era, entity.historical_id): entity
        for entity in entities
        if entity.kind == "usuarios"
    }
    forum_entities = {
        (entity.era, entity.historical_id): entity for entity in entities if entity.kind == "foruns"
    }
    topics = {
        (str(row["era"]), _row_int(row, "topic_id")): row
        for row in database.connection.execute(
            "SELECT era, topic_id, forum_id, title, first_posted_at, last_posted_at FROM topics"
        )
        if (str(row["era"]), _row_int(row, "topic_id")) in topic_entities
    }
    users = {
        (str(row["era"]), _row_int(row, "historical_id")): row
        for row in database.connection.execute(
            "SELECT user_pk, era, historical_id, username, first_posted_at, "
            "last_posted_at, post_count FROM users WHERE historical_id IS NOT NULL"
        )
        if (str(row["era"]), _row_int(row, "historical_id")) in user_entities
    }
    users_by_pk = {_row_int(row, "user_pk"): key for key, row in users.items()}
    forums = {
        (str(row["era"]), _row_int(row, "forum_id")): row
        for row in database.connection.execute(
            "SELECT era, forum_id, name, first_seen, last_seen FROM forums"
        )
        if (str(row["era"]), _row_int(row, "forum_id")) in forum_entities
    }
    written: set[EntityRoute] = set()

    def begin_page(
        handle: IO[str],
        route: PurePosixPath,
        title: str,
        heading: str,
        details: tuple[tuple[str, object | None], ...],
    ) -> None:
        _begin_page(
            handle,
            route,
            title,
            heading,
            details,
            search_available=search_available,
        )

    current_tid: int | None = None
    current_handles: dict[EntityRoute, IO[str]] = {}
    post_rows = database.connection.execute(
        """
        SELECT p.post_pk, p.era, p.topic_id, p.historical_id, p.author_name,
               p.posted_at, p.posted_at_raw, p.body_text, p.user_pk,
               p.best_capture_id, u.historical_id AS author_historical_id
        FROM posts AS p
        LEFT JOIN users AS u ON u.user_pk=p.user_pk
        WHERE p.topic_id IS NOT NULL
        ORDER BY p.topic_id, p.posted_at, p.post_pk
        """
    )

    def _open_topic_handles(tid: int) -> dict[EntityRoute, IO[str]]:
        """Abrir handles para TODAS as entidades que representam este topic_id."""
        handles: dict[EntityRoute, IO[str]] = {}
        for (_era, eid), entity in topic_entities.items():
            if eid != tid:
                continue
            h = _open_page(output, entity)
            t = topics.get((entity.era, tid))
            if t is None:
                for era2 in ("phpbb3", "phpbb2", "snitz", "forum", "comunidade"):
                    t = topics.get((era2, tid))
                    if t:
                        break
            begin_page(
                h,
                entity.route,
                str(t["title"] or f"Tópico {entity.historical_id}") if t else f"Tópico {tid}",
                str(t["title"] or f"Tópico {entity.historical_id}") if t else f"Tópico {tid}",
                (
                    ("Geração", entity.era),
                    ("ID histórico", entity.historical_id),
                    ("Primeira mensagem", t["first_posted_at"] if t else "—"),
                    ("Última mensagem", t["last_posted_at"] if t else "—"),
                ),
            )
            srcs = topic_source_routes.get((entity.era, tid), ())
            if srcs:
                h.write('<nav class="entity-sources"><strong>Capturas históricas:</strong><ul>')
                for src in srcs:
                    h.write(
                        f'<li><a href="{_href(entity.route, src)}">{_clean(src.as_posix())}</a></li>'
                    )
                h.write("</ul></nav>")
            h.write('<section class="entity-posts">')
            handles[entity] = h
        return handles

    def _close_topic_handles(handles: dict[EntityRoute, IO[str]]) -> None:
        for entity, h in handles.items():
            h.write("</section>")
            _end_page(h)
            written.add(entity)

    for row in post_rows:
        tid = _row_int(row, "topic_id")
        if tid != current_tid:
            if current_handles:
                _close_topic_handles(current_handles)
            current_tid = tid
            current_handles = _open_topic_handles(tid)
        if not current_handles:
            continue
        post_id = (
            _row_int(row, "historical_id")
            if row["historical_id"] is not None
            else _row_int(row, "post_pk")
        )
        author_key = None
        if row["author_historical_id"] is not None:
            author_key = str(row["era"]), _row_int(row, "author_historical_id")
        author_entity = user_entities.get(author_key) if author_key else None
        for entity, h in current_handles.items():
            h.write(
                f'<article class="entity-post" id="p{post_id}"><span id="post{post_id}"></span><span id="{post_id}"></span><h2>'
            )
            if author_entity is not None:
                h.write(
                    f'<a href="{_href(entity.route, author_entity.route)}">{_clean(row["author_name"])}</a>'
                )
            else:
                h.write(_clean(row["author_name"]))
            h.write(f"</h2><time>{_clean(row['posted_at'] or row['posted_at_raw'])}</time>")
            h.write(f'<div class="entity-post-body">{_clean(row["body_text"])}</div>')
            source = source_routes_by_capture.get(_row_int(row, "best_capture_id"))
            if source is not None:
                h.write(
                    f'<p><a href="{_href(entity.route, source)}">Ver na captura histórica</a></p>'
                )
            h.write("</article>")
    if current_handles:
        _close_topic_handles(current_handles)
    # Fallback: páginas de tópicos sem posts em nenhuma era
    for (era, tid), entity in topic_entities.items():
        if entity in written:
            continue
        topic = topics.get((era, tid))
        if topic is None:
            continue
        handle = _open_page(output, entity)
        begin_page(
            handle,
            entity.route,
            str(topic["title"] or f"Tópico {entity.historical_id}"),
            str(topic["title"] or f"Tópico {entity.historical_id}"),
            (
                ("Geração", entity.era),
                ("ID histórico", entity.historical_id),
                ("Primeira evidência", topic["first_posted_at"]),
                ("Última evidência", topic["last_posted_at"]),
            ),
        )
        handle.write("<p>Nenhuma mensagem completa deste tópico foi recuperada.</p>")
        _end_page(handle)
        written.add(entity)

    current_user: tuple[str, int] | None = None
    current_entity = None
    handle = None
    user_post_rows = database.connection.execute(
        """
        SELECT p.user_pk, p.era, p.topic_id, p.historical_id, p.post_pk,
               p.topic_title, p.posted_at, p.posted_at_raw
        FROM posts AS p
        WHERE p.user_pk IS NOT NULL
        ORDER BY p.user_pk, p.posted_at, p.post_pk
        """
    )
    for row in user_post_rows:
        key = users_by_pk.get(_row_int(row, "user_pk"))
        if key is None:
            continue
        entity = user_entities[key]
        if key != current_user:
            if handle is not None:
                handle.write("</ol>")
                _end_page(handle)
            current_user = key
            current_entity = entity
            handle = _open_page(output, entity)
            user = users[key]
            begin_page(
                handle,
                entity.route,
                str(user["username"]),
                str(user["username"]),
                (
                    ("Geração", entity.era),
                    ("ID histórico", entity.historical_id),
                    ("Primeira mensagem", user["first_posted_at"]),
                    ("Última mensagem", user["last_posted_at"]),
                    ("Mensagens verificadas", user["post_count"]),
                ),
            )
            handle.write('<ol class="entity-list">')
            written.add(entity)
        if handle is None or current_entity is None:
            continue
        topic_entity = None
        if row["topic_id"] is not None:
            topic_entity = topic_entities.get((str(row["era"]), _row_int(row, "topic_id")))
        label = row["topic_title"] or f"Mensagem {row['historical_id'] or row['post_pk']}"
        handle.write("<li>")
        if topic_entity is not None:
            post_id = (
                _row_int(row, "historical_id")
                if row["historical_id"] is not None
                else _row_int(row, "post_pk")
            )
            handle.write(
                f'<a href="{_href(current_entity.route, topic_entity.route, f"p{post_id}")}">{_clean(label)}</a>'
            )
        else:
            handle.write(_clean(label))
        handle.write(f" <time>{_clean(row['posted_at'] or row['posted_at_raw'])}</time></li>")
    if handle is not None:
        handle.write("</ol>")
        _end_page(handle)
    for key, entity in user_entities.items():
        if entity in written:
            continue
        user = users[key]
        handle = _open_page(output, entity)
        begin_page(
            handle,
            entity.route,
            str(user["username"]),
            str(user["username"]),
            (
                ("Geração", entity.era),
                ("ID histórico", entity.historical_id),
                ("Mensagens verificadas", user["post_count"]),
            ),
        )
        handle.write("<p>Nenhuma mensagem completa deste usuário foi recuperada.</p>")
        _end_page(handle)
        written.add(entity)

    current_forum: tuple[str, int] | None = None
    current_entity = None
    handle = None
    forum_topics = database.connection.execute(
        """
        SELECT era, forum_id, topic_id, title, first_posted_at, last_posted_at
        FROM topics
        WHERE forum_id IS NOT NULL
        ORDER BY era, forum_id, last_posted_at, topic_id
        """
    )
    for row in forum_topics:
        key = str(row["era"]), _row_int(row, "forum_id")
        entity = forum_entities.get(key)
        if entity is None:
            continue
        if key != current_forum:
            if handle is not None:
                handle.write("</ol>")
                _end_page(handle)
            current_forum = key
            current_entity = entity
            handle = _open_page(output, entity)
            forum = forums[key]
            begin_page(
                handle,
                entity.route,
                str(forum["name"] or f"Fórum {entity.historical_id}"),
                str(forum["name"] or f"Fórum {entity.historical_id}"),
                (
                    ("Geração", entity.era),
                    ("ID histórico", entity.historical_id),
                    ("Primeira evidência", forum["first_seen"]),
                    ("Última evidência", forum["last_seen"]),
                ),
            )
            handle.write('<ol class="entity-list">')
            written.add(entity)
        if handle is None or current_entity is None:
            continue
        topic_entity = topic_entities.get((str(row["era"]), _row_int(row, "topic_id")))
        handle.write("<li>")
        if topic_entity is not None:
            handle.write(
                f'<a href="{_href(current_entity.route, topic_entity.route)}">{_clean(row["title"] or f"Tópico {row['topic_id']}")}</a>'
            )
        else:
            handle.write(_clean(row["title"] or f"Tópico {row['topic_id']}"))
        handle.write(
            f" <time>{_clean(row['last_posted_at'] or row['first_posted_at'])}</time></li>"
        )
    if handle is not None:
        handle.write("</ol>")
        _end_page(handle)
    for key, entity in forum_entities.items():
        if entity in written:
            continue
        forum = forums[key]
        handle = _open_page(output, entity)
        begin_page(
            handle,
            entity.route,
            str(forum["name"] or f"Fórum {entity.historical_id}"),
            str(forum["name"] or f"Fórum {entity.historical_id}"),
            (("Geração", entity.era), ("ID histórico", entity.historical_id)),
        )
        handle.write("<p>Nenhum tópico verificável deste fórum foi recuperado.</p>")
        _end_page(handle)
        written.add(entity)
    if len(written) != len(entity_by_key):
        missing = sorted(entity.route.as_posix() for entity in set(entities) - written)
        raise ValueError(f"páginas consolidadas não geradas: {missing[:3]}")
    return len(written)
