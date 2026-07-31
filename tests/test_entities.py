from pathlib import PurePosixPath

from unidev_archive.database import ArchiveDB
from unidev_archive.entities import plan_entity_fallbacks, write_entity_pages
from unidev_archive.routing import RouteRegistry


def test_plans_navigable_fallback_only_for_verified_entity(tmp_path) -> None:
    database_path = tmp_path / "archive.sqlite3"
    with ArchiveDB(database_path) as database:
        database.initialize()
        database.connection.execute(
            """
            INSERT INTO topics (
                era, topic_id, forum_id, title, first_posted_at,
                last_posted_at, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "phpbb3",
                42,
                None,
                "Tópico preservado",
                "2011-01-01T00:00:00",
                "2011-01-02T00:00:00",
                "20110101000000",
                "20110102000000",
            ),
        )
        timestamp = "20110103000000"
        existing_url = "http://unidev.com.br/phpbb3/viewtopic.php?t=42&start=15"
        absent_url = "http://unidev.com.br/phpbb3/viewtopic.php?t=999"
        plan = plan_entity_fallbacks(
            database,
            RouteRegistry.from_entries([]),
            {1: ((existing_url, "page"), (absent_url, "page"))},
            {1: timestamp},
        )

        assert plan.resolved_urls == 1
        assert plan.aliases == (
            (
                existing_url,
                timestamp,
                PurePosixPath("acervo", "phpbb3", "topicos", "42", "index.html"),
            ),
        )
        assert tuple(entity.route for entity in plan.entities) == (
            PurePosixPath("acervo", "phpbb3", "topicos", "42", "index.html"),
        )

        registry = RouteRegistry.from_mapped_entries(plan.aliases)
        resolution = registry.resolve(existing_url, timestamp)
        assert resolution is not None
        assert resolution.path == plan.entities[0].route
        assert registry.resolve(absent_url, timestamp) is None

        output = tmp_path / "dist"
        (output / "assets").mkdir(parents=True)
        (output / "assets" / "archive-entities.css").write_text("", encoding="utf-8")
        written = write_entity_pages(database, output, plan.entities, {}, {})

    page = output / "acervo" / "phpbb3" / "topicos" / "42" / "index.html"
    published = page.read_text(encoding="utf-8")
    assert written == 1
    assert "Tópico preservado" in published
    assert "Visão consolidada" in published
    assert "data-pagefind-body" not in published
