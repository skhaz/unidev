# pyright: reportMissingImports=false
from __future__ import annotations

from unidev_archive.urls import (
    canonical_url,
    era_for_url,
    is_forum_url,
    resolve_references,
    resource_kind,
)


def test_canonical_url_removes_session_fragment_and_default_port() -> None:
    url = "https://www.forum.unidev.com.br:80/phpbb2/viewtopic.php?sid=abc&t=37733&start=0#p264891"

    assert canonical_url(url) == "http://forum.unidev.com.br/phpbb2/viewtopic.php?t=37733"


def test_canonical_url_repairs_historical_backslash_equals_escape() -> None:
    assert canonical_url("http://unidev.com.br/forum/topic.asp?TOPIC_ID\\07510426") == (
        "http://unidev.com.br/forum/topic.asp?TOPIC_ID=10426"
    )


def test_forum_scope_and_era_cover_all_three_historical_engines() -> None:
    assert era_for_url("http://www.unidev.com.br/forum/topic.asp?TOPIC_ID=1") == "snitz"
    assert era_for_url("http://forum.unidev.com.br/phpbb2/viewtopic.php?t=1") == "phpbb2"
    assert era_for_url("http://unidev.com.br/phpbb3/viewtopic.php?t=1") == "phpbb3"
    assert era_for_url("http://forum.unidev.com.br/forums/thread/37.aspx", "200701") == (
        "community-server"
    )
    assert is_forum_url("http://forum.unidev.com.br/", "200808")
    assert not is_forum_url("http://unidev.com.br/noticias/1")


def test_classifies_assets_and_attachments_including_external_images() -> None:
    assert resource_kind("http://unidev.com.br/phpbb3/styles/theme.css") == "asset"
    assert resource_kind("http://imageshack.us/avatar.gif") == "asset"
    assert resource_kind("http://unidev.com.br/phpbb3/download/file.php?id=2") == "attachment"
    assert resource_kind("http://unidev.com.br/phpbb3/files/demo.zip") == "attachment"


def test_resolve_references_deduplicates_and_ignores_active_pseudo_urls() -> None:
    references = [
        "templates/darkside/main.css",
        "./templates/darkside/main.css",
        "javascript:alert(1)",
        "mailto:user@example.org",
        "https://images.example/avatar.png",
    ]

    assert resolve_references(
        "http://forum.unidev.com.br/phpbb2/viewtopic.php?t=1", references
    ) == (
        "http://forum.unidev.com.br/phpbb2/templates/darkside/main.css",
        "http://images.example/avatar.png",
    )
