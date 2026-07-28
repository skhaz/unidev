# pyright: reportMissingImports=false
from __future__ import annotations

import json
from pathlib import Path

from unidev_archive.database import ArchiveDB, CaptureRecord
from unidev_archive.models import ParsedPage, ParsedPost
from unidev_archive.site import build_site


def test_builds_utf8_static_pages_for_pagefind(tmp_path: Path) -> None:
    database_path = tmp_path / "archive.sqlite3"
    output = tmp_path / "dist"
    record = CaptureRecord(
        "20070804224715",
        "http://forum.unidev.com.br/phpbb2/viewtopic.php?f=9&t=37733",
        200,
        "text/html",
    )
    page = ParsedPage(
        era="phpbb2",
        topic_id=37733,
        forum_id=9,
        topic_title="DirectX no GCC",
        forum_name="DirectX",
        source_encoding="windows-1252",
        posts=(
            ParsedPost(
                topic_id=37733,
                forum_id=9,
                post_id=264891,
                author_id=16263,
                author_name="skhaz",
                posted_at="2007-03-28T18:23:00",
                posted_at_raw="Qua Mar 28, 2007 6:23 pm",
                body_html="Programação em C e C++.",
                body_text="Programação em C e C++.",
            ),
        ),
        references=(),
    )
    with ArchiveDB(database_path) as database:
        database.initialize()
        database.add_captures((record,))
        database.ingest_page(database.capture_id(record.original_url, record.timestamp), page)

        stats = build_site(database, output)

    assert stats.posts == stats.topics == stats.users == 1
    expected = [
        output / "index.html",
        output / "posts" / "1.html",
        output / "topicos" / "37733.html",
        output / "usuarios" / "16263.html",
        output / "assets" / "search.js",
    ]
    assert all(path.is_file() for path in expected)
    post = (output / "posts" / "1.html").read_text(encoding="utf-8")
    assert '<meta charset="utf-8">' in post
    assert "Programação em C e C++." in post
    assert "ProgramaÃ" not in post
    assert "data-pagefind-body" in post
    assert 'data-pagefind-filter="autor">skhaz</span>' in post
    user = (output / "usuarios" / "16263.html").read_text(encoding="utf-8")
    assert "Primeira atividade preservada" in user
    assert "28/03/2007 18:23" in user
    assert all(
        "web.archive.org" not in path.read_text(encoding="utf-8")
        for path in output.rglob("*.html")
    )
    manifest = json.loads((output / "build-manifest.json").read_text(encoding="utf-8"))
    assert manifest["encoding"] == "UTF-8"
    assert manifest["search"] == "Pagefind"
