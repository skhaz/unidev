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


def test_ingest_deduplicates_migrated_post_and_tracks_both_sources(tmp_path: Path) -> None:
    records = [
        CaptureRecord(
            "20070804224715",
            "http://forum.unidev.com.br/phpbb2/viewtopic.php?t=37733",
            200,
            "text/html",
        ),
        CaptureRecord(
            "20090704013632", "http://unidev.com.br/phpbb3/viewtopic.php?t=37733", 200, "text/html"
        ),
    ]
    with ArchiveDB(tmp_path / "archive.sqlite3") as database:
        database.initialize()
        database.add_captures(records)
        for record in records:
            database.ingest_page(
                database.capture_id(record.original_url, record.timestamp), page_for_skhaz()
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

        assert database.ingest_listings(capture_id, page) == 1

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
