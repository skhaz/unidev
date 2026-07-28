# pyright: reportMissingImports=false
"""Generate the complete GitHub Pages site; no runtime server is required."""

from __future__ import annotations

import html
import itertools
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from unidev_archive.database import ArchiveDB

_POSTS_SQL = """
SELECT p.*, u.historical_id AS user_historical_id, u.username,
       c.original_url, c.timestamp AS capture_timestamp
FROM posts AS p
JOIN users AS u ON u.user_pk=p.user_pk
JOIN captures AS c ON c.capture_id=p.best_capture_id
ORDER BY p.post_pk
"""
_TOPIC_POSTS_SQL = """
SELECT p.*, u.historical_id AS user_historical_id, u.username
FROM posts AS p JOIN users AS u ON u.user_pk=p.user_pk
WHERE p.topic_id IS NOT NULL
ORDER BY p.topic_id, p.posted_at, p.post_pk
"""
_ACTIVITY_TOPICS_SQL = """
SELECT t.topic_id, t.title, f.name AS forum_name, a.role, a.post_id,
       a.posted_at, u.username, c.timestamp AS capture_timestamp,
       c.original_url
FROM topics AS t
JOIN activity_evidence AS a ON a.topic_id=t.topic_id
JOIN users AS u ON u.user_pk=a.user_pk
JOIN captures AS c ON c.capture_id=a.best_capture_id
LEFT JOIN forums AS f ON f.forum_id=t.forum_id
WHERE NOT EXISTS (SELECT 1 FROM posts WHERE posts.topic_id=t.topic_id)
ORDER BY t.topic_id, a.posted_at, a.identity
"""
_USER_POSTS_SQL = """
SELECT * FROM (
    SELECT u.user_pk, u.historical_id AS user_historical_id, u.username,
           u.first_posted_at, u.last_posted_at, u.post_count,
           p.post_pk, p.topic_id, p.author_name, p.topic_title, p.forum_name,
           p.posted_at, p.body_html, 'post' AS event_type,
           NULL AS role, NULL AS evidence_post_id
    FROM users AS u LEFT JOIN posts AS p ON p.user_pk=u.user_pk
    UNION ALL
    SELECT u.user_pk, u.historical_id AS user_historical_id, u.username,
           u.first_posted_at, u.last_posted_at, u.post_count,
           NULL AS post_pk, a.topic_id, u.username AS author_name,
           a.topic_title, a.forum_name, a.posted_at, NULL AS body_html,
           'activity' AS event_type, a.role, a.post_id AS evidence_post_id
    FROM users AS u JOIN activity_evidence AS a ON a.user_pk=u.user_pk
)
ORDER BY user_pk, posted_at DESC, post_pk DESC
"""
_FORUMS_SQL = "SELECT forum_id, name FROM forums ORDER BY name"


@dataclass(frozen=True, slots=True)
class BuildStats:
    posts: int
    topics: int
    users: int
    activities: int
    files: int


def _escape(value: object | None) -> str:
    return html.escape(str(value or ""), quote=True)


def _date_label(value: str | None) -> str:
    if not value:
        return "Data não recuperada"
    return f"{value[8:10]}/{value[5:7]}/{value[:4]} {value[11:16]}"


def _page(
    title: str,
    content: str,
    *,
    description: str = "Arquivo histórico do fórum UniDev",
    indexed: bool = False,
    metadata: dict[str, str] | None = None,
    filters: dict[str, str] | None = None,
) -> str:
    pagefind = []
    for key, value in (metadata or {}).items():
        pagefind.append(
            f'<span data-pagefind-meta="{_escape(key)}">{_escape(value)}</span>'
        )
    for key, value in (filters or {}).items():
        pagefind.append(
            f'<span data-pagefind-filter="{_escape(key)}">{_escape(value)}</span>'
        )
    pagefind_data = (
        '<div class="pagefind-data" aria-hidden="true">' + "".join(pagefind) + "</div>"
        if pagefind
        else ""
    )
    body_attribute = " data-pagefind-body" if indexed else ""
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{_escape(description)}">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; form-action 'none'; base-uri 'none'">
<title>{_escape(title)} · Arquivo UniDev</title>
<link rel="stylesheet" href="/unidev/assets/site.css">
</head>
<body>
<header class="site-header">
  <a class="brand" href="/unidev/">UniDev <span>arquivo 2000–2009</span></a>
  <nav><a href="/unidev/">Busca</a><a href="/unidev/usuarios/">Usuários</a></nav>
</header>
<main{body_attribute}>{pagefind_data}{content}</main>
<footer>Restauração histórica independente, somente leitura. Datas refletem o fórum e podem variar por fuso.</footer>
</body>
</html>
"""


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def _post_url(row: sqlite3.Row) -> str:
    return f"/unidev/posts/{int(row['post_pk'])}.html"


def _topic_url(topic_id: int) -> str:
    return f"/unidev/topicos/{topic_id}.html"


def _user_url(row: sqlite3.Row) -> str:
    identifier = row["user_historical_id"] or row["user_pk"]
    return f"/unidev/usuarios/{int(identifier)}.html"


def _post_card(row: sqlite3.Row, *, include_body: bool) -> str:
    topic_id = row["topic_id"]
    title = row["topic_title"] or f"Tópico {topic_id or 'sem ID'}"
    body = f'<div class="post-content">{row["body_html"]}</div>' if include_body else ""
    topic_link = _topic_url(int(topic_id)) if topic_id is not None else _post_url(row)
    return f"""
<article class="post" id="post-{int(row["post_pk"])}">
  <header>
    <h2><a href="{topic_link}">{_escape(title)}</a></h2>
    <p><a class="author" href="{_user_url(row)}">{_escape(row["author_name"])}</a>
       · <time datetime="{_escape(row["posted_at"])}">{_escape(_date_label(row["posted_at"]))}</time>
       · {_escape(row["forum_name"] or "Fórum não identificado")}</p>
  </header>
  {body}
</article>
"""


def _build_post_pages(database: ArchiveDB, output: Path) -> int:
    count = 0
    for row in database.connection.execute(_POSTS_SQL):
        title = row["topic_title"] or f"Mensagem de {row['author_name']}"
        year = str(row["posted_at"] or "")[:4]
        content = f"""
<nav class="breadcrumbs"><a href="/unidev/">Busca</a> / mensagem</nav>
<article class="post-detail">
  <h1>{_escape(title)}</h1>
  <p class="byline">por <a href="{_user_url(row)}">{_escape(row["author_name"])}</a>
  · <time datetime="{_escape(row["posted_at"])}">{_escape(_date_label(row["posted_at"]))}</time></p>
  <div class="post-content">{row["body_html"]}</div>
  <p><a href="{_topic_url(int(row["topic_id"])) if row["topic_id"] is not None else "#"}">Ver contexto do tópico</a></p>
</article>
"""
        page = _page(
            str(title),
            content,
            indexed=True,
            metadata={
                "tipo": "post",
                "autor": str(row["author_name"]),
                "forum": str(row["forum_name"] or ""),
                "data": str(row["posted_at"] or ""),
                "topic_id": str(row["topic_id"] or ""),
            },
            filters={
                "tipo": "post",
                "autor": str(row["author_name"]),
                "forum": str(row["forum_name"] or "Não identificado"),
                "ano": year or "Sem data",
            },
        )
        _write(output / "posts" / f"{int(row['post_pk'])}.html", page)
        count += 1
    return count


def _build_topic_pages(database: ArchiveDB, output: Path) -> int:
    count = 0
    rows = database.connection.execute(_TOPIC_POSTS_SQL)
    for topic_id, group in itertools.groupby(rows, key=lambda row: int(row["topic_id"])):
        posts = list(group)
        first = posts[0]
        title = first["topic_title"] or f"Tópico {topic_id}"
        cards = "".join(_post_card(row, include_body=True) for row in posts)
        content = f"""
<nav class="breadcrumbs"><a href="/unidev/">Busca</a> / {_escape(first["forum_name"])}</nav>
<h1>{_escape(title)}</h1>
<p>{len(posts)} mensagem(ns) recuperada(s)</p>
<section class="thread">{cards}</section>
"""
        _write(output / "topicos" / f"{topic_id}.html", _page(str(title), content))
        count += 1

    activity_rows = database.connection.execute(_ACTIVITY_TOPICS_SQL, ())
    for topic_id, group in itertools.groupby(
        activity_rows, key=lambda row: int(row["topic_id"])
    ):
        evidence = list(group)
        first = evidence[0]
        events = "".join(
            f'<li>{_escape(row["username"])} · '
            f'{"autor do tópico" if row["role"] == "topic_author" else "última mensagem"} · '
            f'{_escape(_date_label(row["posted_at"]))} · '
            f'<span>fonte local preservada ({_escape(row["capture_timestamp"])})</span></li>'
            for row in evidence
        )
        title = first["title"] or f"Tópico {topic_id}"
        content = f"""
<nav class="breadcrumbs"><a href="/unidev/">Busca</a> / {_escape(first["forum_name"])}</nav>
<h1>{_escape(title)}</h1>
<p class="notice">A listagem e os metadados deste tópico foram preservados, mas a página com o texto completo ainda não foi localizada.</p>
<ul>{events}</ul>
"""
        _write(
            output / "topicos" / f"{topic_id}.html",
            _page(str(title), content),
        )
        count += 1
    return count


def _build_user_pages(database: ArchiveDB, output: Path) -> int:
    count = 0
    rows = database.connection.execute(_USER_POSTS_SQL)
    index_items: list[str] = []
    for user_pk, group in itertools.groupby(rows, key=lambda row: int(row["user_pk"])):
        user_rows = list(group)
        first = user_rows[0]
        username = str(first["username"])
        posts = [row for row in user_rows if row["event_type"] == "post" and row["post_pk"] is not None]
        activities = [row for row in user_rows if row["event_type"] == "activity"]
        identifier = first["user_historical_id"] or user_pk
        index_items.append(
            f'<li><a href="{int(identifier)}.html">{_escape(username)}</a> '
            f"<span>{len(posts)} post(s)</span></li>"
        )
        links = "".join(_post_card(row, include_body=False) for row in posts)
        evidence = "".join(
            f'<li><time datetime="{_escape(row["posted_at"])}">'
            f'{_escape(_date_label(row["posted_at"]))}</time> · '
            f'<a href="{_topic_url(int(row["topic_id"]))}">{_escape(row["topic_title"])}</a> · '
            f'{"autor do tópico" if row["role"] == "topic_author" else "última mensagem preservada"}'
            f'{f" (post #{int(row["evidence_post_id"])})" if row["evidence_post_id"] else ""}</li>'
            for row in activities
        )
        content = f"""
<nav class="breadcrumbs"><a href="/unidev/">Busca</a> / usuários</nav>
<section class="user-profile">
  <h1>{_escape(username)}</h1>
  <dl><dt>ID histórico</dt><dd>{_escape(first["user_historical_id"] or "não recuperado")}</dd>
      <dt>Primeira atividade preservada</dt><dd>{_escape(_date_label(first["first_posted_at"]))}</dd>
      <dt>Última atividade preservada</dt><dd>{_escape(_date_label(first["last_posted_at"]))}</dd>
      <dt>Posts completos recuperados</dt><dd>{len(posts)}</dd>
      <dt>Evidências em listagens</dt><dd>{len(activities)}</dd></dl>
</section>
<section data-pagefind-ignore="all"><h2>Atividade preservada</h2><ul>{evidence}</ul>
<h2>Mensagens completas</h2>{links}</section>
"""
        page = _page(
            username,
            content,
            indexed=True,
            metadata={"tipo": "usuario", "autor": username},
            filters={"tipo": "usuario"},
        )
        _write(output / "usuarios" / f"{int(identifier)}.html", page)
        count += 1
    _write(
        output / "usuarios" / "index.html",
        _page(
            "Usuários recuperados",
            '<h1>Usuários recuperados</h1><ul class="user-list">' + "".join(index_items) + "</ul>",
        ),
    )
    return count


def _build_home(database: ArchiveDB, output: Path) -> None:
    counts = database.counts()
    options = "".join(
        f'<option value="{_escape(row["name"] or "Não identificado")}">{_escape(row["name"] or "Não identificado")}</option>'
        for row in database.connection.execute(_FORUMS_SQL)
    )
    content = f"""
<section class="hero">
  <p class="eyebrow">MEMÓRIA DA COMUNIDADE BRASILEIRA DE GAMEDEV</p>
  <h1>Fórum UniDev<br><span>2000–2009</span></h1>
  <p>Pesquise {counts.get("posts", 0):,} mensagens e {counts.get("users", 0):,} usuários recuperados da Wayback Machine.</p>
</section>
<section class="search-panel" aria-labelledby="search-title">
  <h2 id="search-title">Buscar no arquivo</h2>
  <form id="search-form" role="search">
    <label for="search-input">Termos, código ou nome de usuário</label>
    <div class="search-row"><input id="search-input" name="q" type="search" autocomplete="off" autofocus>
    <button type="submit">Buscar</button></div>
    <div class="filters">
      <label>Tipo<select id="type-filter"><option value="">Tudo</option><option value="post">Posts</option><option value="usuario">Usuários</option></select></label>
      <label>Fórum<select id="forum-filter"><option value="">Todos</option>{options}</select></label>
      <label>Ano<select id="year-filter"><option value="">Todos</option>{"".join(f"<option>{year}</option>" for year in range(2000, 2010))}</select></label>
    </div>
  </form>
  <p id="search-status" aria-live="polite"></p>
  <ol id="search-results" class="results"></ol>
</section>
<section class="about"><h2>Sobre esta restauração</h2><p>Conteúdo preservado como documento histórico. As páginas são estáticas, a busca roda no navegador e nenhum login ou formulário antigo funciona.</p></section>
<script type="module" src="/unidev/assets/search.js"></script>
"""
    _write(output / "index.html", _page("Busca", content))
    _write(output / "404.html", _page("Página não encontrada", "<h1>Página não encontrada</h1>"))


def _copy_assets(output: Path) -> None:
    package_dir = Path(__file__).with_name("static")
    for filename in ("site.css", "search.js"):
        _write(output / "assets" / filename, (package_dir / filename).read_text(encoding="utf-8"))
    _write(output / ".nojekyll", "")


def _write_build_manifest(output: Path, stats: BuildStats) -> None:
    _write(
        output / "build-manifest.json",
        json.dumps(
            {"encoding": "UTF-8", "search": "Pagefind", **asdict(stats)},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )


def build_site(database: ArchiveDB, output: str | Path) -> BuildStats:
    """Generate deterministic UTF-8 HTML for Pagefind to index in CI."""

    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    _copy_assets(destination)
    posts = _build_post_pages(database, destination)
    topics = _build_topic_pages(database, destination)
    users = _build_user_pages(database, destination)
    _build_home(database, destination)
    files = sum(1 for path in destination.rglob("*") if path.is_file())
    stats = BuildStats(
        posts=posts,
        topics=topics,
        users=users,
        activities=database.counts().get("activities", 0),
        files=files + 1,
    )
    _write_build_manifest(destination, stats)
    return stats
