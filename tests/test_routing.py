# pyright: reportMissingImports=false
from __future__ import annotations

from pathlib import PurePosixPath

from unidev_archive.routing import RouteRegistry, static_route


def test_rejects_nonforum_unidev_pages_as_out_of_scope() -> None:
    for url in (
        "http://www.unidev.com.br/",
        "http://www.unidev.com.br/noticias/1.asp",
    ):
        assert static_route(url) is None
        assert RouteRegistry.from_urls((url,)).resolve(url) is None


def test_routes_snitz_topics_forums_profiles_and_pagination() -> None:
    assert static_route("http://www.unidev.com.br/forum/topic.asp?TOPIC_ID=10426") == PurePosixPath(
        "forum/topicos/10426/index.html"
    )
    assert static_route(
        "http://www.unidev.com.br/forum/topic.asp?whichpage=1&TOPIC_ID=10426"
    ) == PurePosixPath("forum/topicos/10426/index.html")
    assert static_route(
        "http://www.unidev.com.br/forum/topic.asp?whichpage=2&TOPIC_ID=10426"
    ) == PurePosixPath("forum/topicos/10426/pagina/2/index.html")
    assert static_route(
        "http://www.unidev.com.br/forum/forum.asp?FORUM_ID=12&whichpage=3"
    ) == PurePosixPath("forum/foruns/12/pagina/3/index.html")
    assert static_route(
        "http://www.unidev.com.br/forum/pop_profile.asp?id=99&mode=display"
    ) == PurePosixPath("forum/usuarios/99/index.html")


def test_routes_community_server_threads_forums_and_permalinks() -> None:
    thread = PurePosixPath("comunidade/topicos/37/index.html")
    assert (
        static_route("http://forum.unidev.com.br/forums/thread/37.aspx", "20070401000000") == thread
    )
    assert (
        static_route(
            "http://forum.unidev.com.br/forums/permalink/37/88/ShowThread.aspx#88",
            "20070401000000",
        )
        == thread
    )
    assert static_route(
        "http://forum.unidev.com.br/forums/5/ShowForum.aspx", "20070401000000"
    ) == PurePosixPath("comunidade/foruns/5/index.html")


def test_routes_phpbb_pages_without_query_order_or_session_collisions() -> None:
    assert static_route("http://unidev.com.br/phpbb3/") == PurePosixPath("phpbb3/index.html")
    first = static_route(
        "http://forum.unidev.com.br/phpbb2/viewtopic.php?sid=dead&f=12&t=37733&start=0"
    )
    reordered = static_route("http://forum.unidev.com.br/phpbb2/viewtopic.php?t=37733&f=12")
    assert first == reordered == PurePosixPath("phpbb2/topicos/37733/index.html")
    assert static_route(
        "http://forum.unidev.com.br/phpbb2/viewtopic.php?hilit=001&postdays=0&postorder=ASC&t=37733"
    ) == PurePosixPath("phpbb2/topicos/37733/index.html")
    assert static_route(
        "http://unidev.com.br/phpbb3/viewtopic.php?t=48797&start=15#p123"
    ) == PurePosixPath("phpbb3/topicos/48797/inicio/15/index.html")
    assert static_route(
        "http://unidev.com.br/phpbb3/viewtopic.php?t=48797&start=1"
    ) == PurePosixPath("phpbb3/topicos/48797/inicio/1/index.html")
    assert static_route(
        "http://unidev.com.br/phpbb3/viewtopic.php?t=48797&view=print"
    ) == PurePosixPath("phpbb3/topicos/48797/visualizacao/print/index.html")
    assert static_route("http://unidev.com.br/phpbb3/viewtopic.php?t=48797&p=1") != static_route(
        "http://unidev.com.br/phpbb3/viewtopic.php?t=48797&p=2"
    )
    assert static_route(
        "http://unidev.com.br/phpbb3/viewtopic.php?t=48797&view=next"
    ) != static_route("http://unidev.com.br/phpbb3/viewtopic.php?t=48797&view=previous")
    assert static_route("http://unidev.com.br/phpbb3/viewforum.php?start=50&f=19") == PurePosixPath(
        "phpbb3/foruns/19/inicio/50/index.html"
    )
    assert static_route(
        "http://unidev.com.br/phpbb3/viewtopic.php?p=342938#p342938"
    ) == PurePosixPath("phpbb3/posts/342938/index.html")
    assert static_route(
        "http://unidev.com.br/phpbb3/memberlist.php?mode=viewprofile&u=16263"
    ) == PurePosixPath("phpbb3/usuarios/16263/index.html")
    assert static_route(
        "http://unidev.com.br/phpbb3/memberlist.php?mode=group&u=16263"
    ) != PurePosixPath("phpbb3/usuarios/16263/index.html")


def test_historical_write_actions_share_read_only_local_pages() -> None:
    assert static_route(
        "http://unidev.com.br/phpbb3/posting.php?mode=reply&f=81&t=55715"
    ) == PurePosixPath("phpbb3/acoes/somente-leitura/index.html")
    assert static_route(
        "http://forum.unidev.com.br/phpbb2/login.php?redirect=viewtopic.php"
    ) == PurePosixPath("phpbb2/acoes/login/index.html")
    assert static_route(
        "http://forum.unidev.com.br/forums/addpost.aspx?ForumID=1&ReportPostID=42",
        "20050101000000",
    ) == PurePosixPath("comunidade/acoes/somente-leitura/index.html")
    assert static_route(
        "http://forum.unidev.com.br/phpbb2/viewforum.php?f=1&mark=topics"
    ) == PurePosixPath("phpbb2/acoes/somente-leitura/index.html")


def test_historical_search_variants_share_the_working_static_search() -> None:
    assert static_route(
        "http://unidev.com.br/phpbb3/search.php?keywords=C%2B%2B&author=skhaz"
    ) == PurePosixPath("busca/index.html")
    assert static_route("http://www.unidev.com.br/forum/search.asp") == PurePosixPath(
        "busca/index.html"
    )
    assert static_route(
        "http://forum.unidev.com.br/search/SearchResults.aspx?u=2103&o=DateDescending",
        "20070401000000",
    ) == PurePosixPath("busca/index.html")


def test_unknown_content_query_gets_stable_collision_resistant_route() -> None:
    ascending = static_route(
        "http://unidev.com.br/phpbb3/portal_pages.php?page=C%2B%2B&author=skhaz"
    )
    reordered = static_route(
        "http://unidev.com.br/phpbb3/portal_pages.php?author=skhaz&page=C%2B%2B"
    )
    different = static_route(
        "http://unidev.com.br/phpbb3/portal_pages.php?author=outro&page=C%2B%2B"
    )
    assert ascending == reordered
    assert different != ascending
    assert ascending is not None
    assert ascending.parts[:3] == ("phpbb3", "paginas", "portal_pages")


def test_registry_uses_capture_timestamp_for_community_server_routes() -> None:
    url = "http://forum.unidev.com.br/default.aspx"
    registry = RouteRegistry.from_entries(((url, "20050101000000"),))

    resolved = registry.resolve(url, "20050101000000")

    assert resolved is not None
    assert resolved.path == PurePosixPath("comunidade/index.html")
    assert registry.resolve(url, "20080101000000") is None


def test_registry_maps_dynamic_previous_view_to_rendered_topic() -> None:
    requested = "http://forum.unidev.com.br/phpbb2/viewtopic.php?t=35996&view=previous"
    registry = RouteRegistry.from_mapped_entries(
        (
            (
                requested,
                "20070826223002",
                PurePosixPath("phpbb2/topicos/7574/index.html"),
            ),
        )
    )

    resolved = registry.resolve(requested, "20070827000000")

    assert resolved is not None
    assert resolved.path == PurePosixPath("phpbb2/topicos/7574/index.html")


def test_registry_falls_back_from_missing_print_view_to_captured_normal_view() -> None:
    normal = "http://unidev.com.br/phpbb3/viewtopic.php?f=19&start=15&t=42"
    registry = RouteRegistry.from_urls((normal,))

    resolved = registry.resolve(
        "http://unidev.com.br/phpbb3/viewtopic.php?f=19&start=15&t=42&view=print"
    )

    assert resolved is not None
    assert resolved.path == PurePosixPath("phpbb3/topicos/42/inicio/15/index.html")


def test_registry_does_not_substitute_missing_topic_variant_or_generation() -> None:
    captured = "http://forum.unidev.com.br/phpbb2/viewtopic.php?p=31413"
    registry = RouteRegistry.from_mapped_entries(
        (
            (
                captured,
                "20070828223649",
                PurePosixPath("phpbb2/posts/31413/index.html"),
            ),
        )
    )

    assert (
        registry.resolve(
            "http://forum.unidev.com.br/phpbb2/viewtopic.php?t=7574&start=15",
            "20070829000000",
        )
        is None
    )
    assert (
        registry.resolve(
            "http://unidev.com.br/phpbb3/viewtopic.php?t=7574",
            "20100101000000",
        )
        is None
    )


def test_registry_maps_post_permalink_to_page_containing_the_post() -> None:
    topic = "http://forum.unidev.com.br/phpbb2/viewtopic.php?t=7574"
    route = PurePosixPath("phpbb2/topicos/7574/index.html")
    registry = RouteRegistry.from_mapped_entries(
        ((topic, "20070828223649", route),),
        ((topic, "20070828223649", route, 31413),),
    )

    resolved = registry.resolve(
        "http://forum.unidev.com.br/phpbb2/viewtopic.php?p=31413#31413",
        "20070829000000",
    )

    assert resolved is not None
    assert resolved.path == route
    assert resolved.fragment == "31413"


def test_registry_never_maps_external_phpbb_forum_by_numeric_id() -> None:
    registry = RouteRegistry.from_urls(("http://unidev.com.br/phpbb3/viewtopic.php?t=42",))

    assert registry.resolve("https://example.org/phpbb3/viewtopic.php?t=42") is None


def test_registry_does_not_invent_uncaptured_root_index() -> None:
    registry = RouteRegistry.from_urls(("http://unidev.com.br/phpbb3/viewtopic.php?t=1",))

    assert registry.resolve("http://unidev.com.br/") is None


def test_registry_resolves_only_captured_pages_and_retains_fragment() -> None:
    topic = "http://unidev.com.br/phpbb3/viewtopic.php?t=49617"
    forum = "http://unidev.com.br/phpbb3/viewforum.php?f=19"
    registry = RouteRegistry.from_urls((topic, forum))

    resolved = registry.resolve("http://unidev.com.br/phpbb3/viewtopic.php?f=19&t=49617#p342938")

    assert resolved is not None
    assert resolved.path == PurePosixPath("phpbb3/topicos/49617/index.html")
    assert resolved.fragment == "p342938"
    assert registry.resolve("http://unidev.com.br/phpbb3/viewtopic.php?t=99999") is None
