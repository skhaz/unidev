# pyright: reportMissingImports=false
from __future__ import annotations

from pathlib import Path

from unidev_archive.database import ArchiveDB, CaptureRecord
from unidev_archive.models import ParsedPage, ParsedPost, ParsedTopicListing


def page_for_skhaz(body: str = "Programação em C") -> ParsedPage:
    return ParsedPage(
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
                body_html=f"<p>{body}</p>",
                body_text=body,
            ),
        ),
        references=(),
    )


def test_ingest_deduplicates_repeated_capture_and_tracks_both_sources(tmp_path: Path) -> None:
    records = [
        CaptureRecord(
            "20070804224715",
            "http://forum.unidev.com.br/phpbb2/viewtopic.php?t=37733",
            200,
            "text/html",
        ),
        CaptureRecord(
            "20090704013632",
            "http://forum.unidev.com.br/phpbb2/viewtopic.php?t=37733",
            200,
            "text/html",
        ),
    ]
    with ArchiveDB(tmp_path / "archive.sqlite3") as database:
        database.initialize()
        database.add_captures(records)
        for record in records:
            database.ingest_page(
                database.capture_id(record.original_url, record.timestamp),
                record.timestamp,
                page_for_skhaz(),
            )

        assert database.counts() == {
            "captures": 2,
            "blobs": 0,
            "topics": 1,
            "posts": 1,
            "users": 1,
            "activities": 0,
        }
        post = database.connection.execute("SELECT * FROM posts").fetchone()
        assert post is not None
        assert post["historical_id"] == 264891
        assert post["author_name"] == "skhaz"
        assert post["body_text"] == "Programação em C"
        assert database.connection.execute("SELECT count(*) FROM post_sources").fetchone()[0] == 2
        user = database.connection.execute("SELECT * FROM users").fetchone()
        assert user["historical_id"] == 16263
        assert user["first_posted_at"] == "2007-03-28T18:23:00"
        assert user["last_posted_at"] == "2007-03-28T18:23:00"
        assert user["post_count"] == 1


def test_best_capture_provenance_and_body_advance_atomically(tmp_path: Path) -> None:
    records = (
        CaptureRecord(
            "20070101000000",
            "http://forum.unidev.com.br/phpbb2/viewtopic.php?t=37733",
            200,
            "text/html",
        ),
        CaptureRecord(
            "20110101000000",
            "http://forum.unidev.com.br/phpbb2/viewtopic.php?t=37733&start=0",
            200,
            "text/html",
        ),
    )
    with ArchiveDB(tmp_path / "archive.sqlite3") as database:
        database.initialize()
        database.add_captures(records)
        for record, body in zip(records, ("corpo antigo", "corpo editado"), strict=True):
            database.ingest_page(
                database.capture_id(record.original_url, record.timestamp),
                record.timestamp,
                page_for_skhaz(body),
            )

        post = database.connection.execute(
            "SELECT body_text, best_capture_timestamp FROM posts"
        ).fetchone()
        assert tuple(post) == ("corpo editado", "20110101000000")


def test_ingests_listing_evidence_for_user_activity_period(tmp_path: Path) -> None:
    record = CaptureRecord(
        "20110716171242",
        "http://unidev.com.br/phpbb3/viewforum.php?f=19&start=100",
        200,
        "text/html",
    )
    page = ParsedPage(
        era="phpbb3",
        topic_id=None,
        forum_id=19,
        topic_title=None,
        forum_name="Precisa-se",
        source_encoding="utf-8",
        posts=(),
        references=(),
        listings=(
            ParsedTopicListing(
                topic_id=49617,
                forum_id=19,
                title="Vaga Programador - Curitiba",
                author_id=50739,
                author_name="Make_Wish",
                created_at="2009-09-11T19:35:00",
                last_post_id=342938,
                last_author_id=16263,
                last_author_name="skhaz",
                last_posted_at="2009-09-12T22:25:00",
            ),
        ),
    )
    with ArchiveDB(tmp_path / "archive.sqlite3") as database:
        database.initialize()
        database.add_captures((record,))
        capture_id = database.capture_id(record.original_url, record.timestamp)

        assert database.ingest_listings(capture_id, record.timestamp, page) == 1

        user = database.connection.execute(
            "SELECT * FROM users WHERE username_norm='skhaz'"
        ).fetchone()
        assert user["historical_id"] == 16263
        assert user["first_posted_at"] == "2009-09-12T22:25:00"
        assert user["last_posted_at"] == "2009-09-12T22:25:00"
        evidence = database.connection.execute(
            "SELECT * FROM activity_evidence WHERE user_pk=?", (user["user_pk"],)
        ).fetchone()
        assert evidence["post_id"] == 342938
        assert evidence["topic_title"] == "Vaga Programador - Curitiba"


def test_numeric_entity_ids_are_scoped_to_forum_generation(tmp_path: Path) -> None:
    records = (
        CaptureRecord(
            "20050101000000",
            "http://unidev.com.br/forum/topic.asp?TOPIC_ID=1",
            200,
            "text/html",
        ),
        CaptureRecord(
            "20080101000000",
            "http://forum.unidev.com.br/phpbb2/viewtopic.php?t=1",
            200,
            "text/html",
        ),
    )
    pages = tuple(
        ParsedPage(
            era=era,
            topic_id=1,
            forum_id=1,
            topic_title=title,
            forum_name=title,
            source_encoding="utf-8",
            posts=(
                ParsedPost(
                    topic_id=1,
                    forum_id=1,
                    post_id=42,
                    author_id=42,
                    author_name=author,
                    posted_at=date,
                    posted_at_raw=None,
                    body_html=body,
                    body_text=body,
                ),
            ),
            references=(),
        )
        for era, title, author, date, body in (
            ("snitz", "Legado", "autor antigo", "2005-01-01T00:00:00", "corpo antigo"),
            ("phpbb2", "Novo", "autor novo", "2008-01-01T00:00:00", "corpo novo"),
        )
    )
    with ArchiveDB(tmp_path / "archive.sqlite3") as database:
        database.initialize()
        database.add_captures(records)
        for record, page in zip(records, pages, strict=True):
            database.ingest_page(
                database.capture_id(record.original_url, record.timestamp),
                record.timestamp,
                page,
            )

        assert database.connection.execute("SELECT count(*) FROM forums").fetchone()[0] == 2
        assert database.connection.execute("SELECT count(*) FROM topics").fetchone()[0] == 2
        assert database.connection.execute("SELECT count(*) FROM posts").fetchone()[0] == 2
        assert {row[0] for row in database.connection.execute("SELECT body_text FROM posts")} == {
            "corpo antigo",
            "corpo novo",
        }
        assert database.connection.execute("SELECT count(*) FROM post_sources").fetchone()[0] == 2


def test_user_identity_prefers_generation_and_historical_id_over_name(tmp_path: Path) -> None:
    records = tuple(
        CaptureRecord(
            f"2008010{index}000000",
            f"http://forum.unidev.com.br/phpbb2/viewtopic.php?t={index}",
            200,
            "text/html",
        )
        for index in range(1, 4)
    )
    authors = ((42, "nome antigo"), (42, "nome novo"), (43, "nome novo"))
    with ArchiveDB(tmp_path / "archive.sqlite3") as database:
        database.initialize()
        database.add_captures(records)
        for index, (record, (author_id, author_name)) in enumerate(
            zip(records, authors, strict=True), 1
        ):
            page = ParsedPage(
                era="phpbb2",
                topic_id=index,
                forum_id=1,
                topic_title=str(index),
                forum_name="Fórum",
                source_encoding="utf-8",
                posts=(
                    ParsedPost(
                        topic_id=index,
                        forum_id=1,
                        post_id=index,
                        author_id=author_id,
                        author_name=author_name,
                        posted_at=f"2008-01-0{index}T00:00:00",
                        posted_at_raw=None,
                        body_html=str(index),
                        body_text=str(index),
                    ),
                ),
                references=(),
            )
            database.ingest_page(
                database.capture_id(record.original_url, record.timestamp),
                record.timestamp,
                page,
            )

        users = database.connection.execute(
            "SELECT historical_id, username FROM users ORDER BY historical_id"
        ).fetchall()
        assert [tuple(row) for row in users] == [(42, "nome novo"), (43, "nome novo")]
        assert (
            database.connection.execute("SELECT count(DISTINCT user_pk) FROM posts").fetchone()[0]
            == 2
        )


def test_activity_evidence_fields_advance_with_same_winning_capture(tmp_path: Path) -> None:
    records = (
        CaptureRecord(
            "20110101000000",
            "http://unidev.com.br/phpbb3/viewforum.php?f=19",
            200,
            "text/html",
        ),
        CaptureRecord(
            "20110102000000",
            "http://unidev.com.br/phpbb3/viewforum.php?f=19&start=50",
            200,
            "text/html",
        ),
    )
    with ArchiveDB(tmp_path / "archive.sqlite3") as database:
        database.initialize()
        database.add_captures(records)
        for record, post_id, posted_at in (
            (records[0], 10, "2011-01-01T10:00:00"),
            (records[1], 11, "2011-01-01T11:00:00"),
        ):
            page = ParsedPage(
                era="phpbb3",
                topic_id=None,
                forum_id=19,
                topic_title=None,
                forum_name="Fórum",
                source_encoding="utf-8",
                posts=(),
                references=(),
                listings=(
                    ParsedTopicListing(
                        topic_id=1,
                        forum_id=19,
                        title="Tópico",
                        author_id=None,
                        author_name=None,
                        created_at=None,
                        last_post_id=post_id,
                        last_author_id=42,
                        last_author_name="autor",
                        last_posted_at=posted_at,
                    ),
                ),
            )
            database.ingest_listings(
                database.capture_id(record.original_url, record.timestamp),
                record.timestamp,
                page,
            )

        evidence = database.connection.execute(
            """
            SELECT post_id, posted_at, best_capture_id, best_capture_timestamp
            FROM activity_evidence
            """,
            (),
        ).fetchone()
        assert tuple(evidence) == (
            11,
            "2011-01-01T11:00:00",
            database.capture_id(records[1].original_url, records[1].timestamp),
            "20110102000000",
        )


def test_historical_user_ids_may_repeat_across_forum_generations(tmp_path: Path) -> None:
    records = (
        CaptureRecord(
            "20050101000000",
            "http://unidev.com.br/forum/topic.asp?TOPIC_ID=1",
            200,
            "text/html",
        ),
        CaptureRecord(
            "20080101000000",
            "http://forum.unidev.com.br/phpbb2/viewtopic.php?t=2",
            200,
            "text/html",
        ),
    )
    pages = tuple(
        ParsedPage(
            era=era,
            topic_id=index,
            forum_id=index,
            topic_title=f"Tópico {index}",
            forum_name="Fórum",
            source_encoding="utf-8",
            posts=(
                ParsedPost(
                    topic_id=index,
                    forum_id=index,
                    post_id=index,
                    author_id=42,
                    author_name=username,
                    posted_at=f"200{index}-01-01T00:00:00",
                    posted_at_raw=None,
                    body_html=username,
                    body_text=username,
                ),
            ),
            references=(),
        )
        for index, (era, username) in enumerate(
            (("snitz", "usuario_antigo"), ("phpbb2", "usuario_novo")),
            1,
        )
    )
    with ArchiveDB(tmp_path / "archive.sqlite3") as database:
        database.initialize()
        database.add_captures(records)
        for record, page in zip(records, pages, strict=True):
            database.ingest_page(
                database.capture_id(record.original_url, record.timestamp),
                record.timestamp,
                page,
            )

        assert (
            database.connection.execute(
                "SELECT count(*) FROM users WHERE historical_id=42"
            ).fetchone()[0]
            == 2
        )


def test_pending_capture_kind_filter_is_parameterized(tmp_path: Path) -> None:
    with ArchiveDB(tmp_path / "archive.sqlite3") as database:
        database.initialize()
        database.add_captures(
            [
                CaptureRecord(
                    "20070804224715",
                    "http://forum.unidev.com.br/phpbb2/viewtopic.php?t=1",
                    200,
                    "text/html",
                ),
                CaptureRecord(
                    "20070804224716", "http://forum.unidev.com.br/phpbb2/theme.css", 200, "text/css"
                ),
            ]
        )

        rows = database.pending_captures(("asset",), 10)

        assert [row["kind"] for row in rows] == ["asset"]
