# pyright: reportMissingImports=false
from __future__ import annotations

from pathlib import PurePosixPath

from lxml import html

from unidev_archive.preservation import preserve_document, preserve_stylesheet, preserve_svg
from unidev_archive.routing import RouteRegistry
from unidev_archive.urls import canonical_url


def test_sanitizes_inline_svg_inside_historical_html() -> None:
    preserved = preserve_document(
        b'<html><body><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><foreignObject><script>alert(1)</script></foreignObject><rect fill="url(http://unidev.com.br/a.png)"/><image href="https://example.org/a.png"/><image xlink:href="https://attacker.example/pixel.png"/><use href="#shape"/></svg></body></html>',
        "http://unidev.com.br/phpbb3/index.php",
        PurePosixPath("phpbb3/index.html"),
        RouteRegistry.from_urls(("http://unidev.com.br/phpbb3/index.php",)),
        {},
    )

    assert "script" not in preserved.casefold()
    assert "foreignobject" not in preserved.casefold()
    assert "https://example.org/a.png" not in preserved
    assert "url(http://unidev.com.br/a.png)" not in preserved
    assert "attacker.example" not in preserved
    assert 'href="#shape"' in preserved


def test_sanitizes_active_svg_before_same_origin_publication() -> None:
    value = preserve_svg(
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" onload="alert(1)"><script>alert(2)</script><style>.x{background:image-set("https://example.org/x.png" 1x)}</style><path style="background:cross-fade(url(https://example.org/y.png),red)"/><set href="#shape" attributeName="href" to="https://example.org/"/><image href="https://example.org/a.png"/><image xlink:href="https://attacker.example/pixel.png"/><use href="#shape"/></svg>'
    )

    assert "script" not in value
    assert "onload" not in value
    assert "https://" not in value
    assert "attacker.example" not in value
    assert "<set" not in value
    assert "@import" not in value
    assert 'href="#shape"' in value


def test_rewrites_quoted_css_imports_and_urls_to_local_files() -> None:
    source = "http://unidev.com.br/phpbb3/styles/theme/main.css"
    resources = {
        canonical_url("http://unidev.com.br/phpbb3/styles/theme/colors.css"): PurePosixPath(
            "recursos/colors.css"
        ),
        canonical_url("http://unidev.com.br/phpbb3/styles/theme/images/bg.gif"): PurePosixPath(
            "recursos/bg.gif"
        ),
    }

    value = preserve_stylesheet(
        b'@import "colors.css";body{background:url(images/bg.gif)}',
        source,
        PurePosixPath("recursos/main.css"),
        resources,
    )

    assert '@import url("colors.css")' in value
    assert 'url("bg.gif")' in value


def test_print_view_is_not_indexed_as_duplicate_search_result() -> None:
    source = "http://unidev.com.br/phpbb3/viewtopic.php?t=42&view=print"
    value = preserve_document(
        b"<html><body><p>Mensagem</p></body></html>",
        source,
        PurePosixPath("phpbb3/topicos/42/visualizacao/print/index.html"),
        RouteRegistry.from_urls((source,)),
        {},
    )

    assert html.document_fromstring(value).xpath("//body/@data-pagefind-body") == []


def test_preserves_empty_capture_as_empty_inert_document() -> None:
    source = "http://unidev.com.br/phpbb3/downloads.php"
    value = preserve_document(
        b"",
        source,
        PurePosixPath("phpbb3/downloads/index.html"),
        RouteRegistry.from_urls((source,)),
        {},
    )

    document = html.document_fromstring(value)
    assert document.xpath("//body")[0].text_content() == ""
    assert document.xpath("//meta[@charset='utf-8']")
    assert document.xpath("//meta[@http-equiv='Content-Security-Policy']")
    assert document.xpath("//link[@rel='icon' and @href='data:,']")


def test_neutralizes_malformed_resource_urls() -> None:
    source = "http://unidev.com.br/phpbb3/index.php"
    value = preserve_document(
        b'<html><body><img id="bad" src="http://[invalid"></body></html>',
        source,
        PurePosixPath("phpbb3/index.html"),
        RouteRegistry.from_urls((source,)),
        {},
    )

    image = html.document_fromstring(value).get_element_by_id("bad")
    assert image.get("src") is None
    assert "archive-link-missing" in image.get("class")


def test_neutralizes_uncaptured_css_references_without_network_requests() -> None:
    value = preserve_stylesheet(
        b'@import "missing.css";body{background:url("missing.gif")}i{background:url("")}',
        "http://unidev.com.br/phpbb3/styles/main.css",
        PurePosixPath("recursos/main.css"),
        {},
    )

    assert "@import" not in value
    assert "missing.css" not in value
    assert "missing.gif" not in value
    assert value.count('url("data:,")') == 2


def test_rewrites_import_without_whitespace_and_with_token_comment() -> None:
    source = "http://unidev.com.br/phpbb3/styles/main.css"
    target = canonical_url("http://unidev.com.br/phpbb3/styles/nested.css")
    resources = {target: PurePosixPath("recursos/nested.css")}

    for css in (
        b'@import"nested.css";',
        b'@import/**/"nested.css";',
        b'@import/**/"https://WEB.ARCHIVE.ORG/web/20110101000000/http://unidev.com.br/phpbb3/styles/nested.css";',
    ):
        value = preserve_stylesheet(
            css,
            source,
            PurePosixPath("recursos/main.css"),
            resources,
        )
        assert '@import url("nested.css")' in value
        assert "web.archive.org" not in value.casefold()


def test_preserves_css_comments_and_strings_that_mention_url_syntax() -> None:
    source = b'/* url(comment.gif) */a::before{content:"url(string.gif)"}'

    value = preserve_stylesheet(
        source,
        "http://unidev.com.br/phpbb3/styles/main.css",
        PurePosixPath("recursos/main.css"),
        {},
    )

    assert value == source.decode()


def test_removes_only_dangerous_css_declaration() -> None:
    value = preserve_stylesheet(
        b".legacy{color:#123;behavior:url(iepngfix.htc);background:#fff}",
        "http://unidev.com.br/phpbb3/style.php",
        PurePosixPath("recursos/style.css"),
        {},
    )

    assert "behavior" not in value
    assert "iepngfix" not in value
    assert "color:#123" in value
    assert "background:#fff" in value


def test_drops_escaped_css_network_identifiers() -> None:
    for source in (
        rb"a{background:u\72l(https://example.org/a.png)}",
        rb'@im\70ort "https://example.org/a.css";',
    ):
        assert (
            preserve_stylesheet(
                source,
                "http://unidev.com.br/phpbb3/styles/main.css",
                PurePosixPath("recursos/main.css"),
                {},
            )
            == ""
        )


def test_rewrites_img_srcset_candidates_to_local_resources() -> None:
    source = "http://unidev.com.br/phpbb3/index.php"
    first = canonical_url("http://unidev.com.br/phpbb3/images/a.png")
    second = canonical_url("http://unidev.com.br/phpbb3/images/b.png")

    value = preserve_document(
        b'<html><body><img srcset="images/a.png 1x, images/b.png 2x"></body></html>',
        source,
        PurePosixPath("phpbb3/index.html"),
        RouteRegistry.from_urls((source,)),
        {
            first: PurePosixPath("recursos/a.png"),
            second: PurePosixPath("recursos/b.png"),
        },
    )

    srcset = html.document_fromstring(value).xpath("//img/@srcset")[0]
    assert srcset == "../recursos/a.png 1x, ../recursos/b.png 2x"
    assert "http" not in srcset


def test_preserves_data_url_with_comma_in_img_srcset() -> None:
    source = "http://unidev.com.br/phpbb3/index.php"
    value = preserve_document(
        b'<html><body><img srcset="data:image/png;base64,AAAA 1x"></body></html>',
        source,
        PurePosixPath("phpbb3/index.html"),
        RouteRegistry.from_urls((source,)),
        {},
    )

    assert 'srcset="data:image/png;base64,AAAA 1x"' in value


def test_drops_unsupported_css_network_functions() -> None:
    value = preserve_stylesheet(
        b'a{background-image:image-set("https://example.org/a.png" 1x)}',
        "http://unidev.com.br/phpbb3/styles/main.css",
        PurePosixPath("recursos/main.css"),
        {},
    )

    assert value == ""


def test_rewrites_url_import_once_across_output_directories() -> None:
    source = "http://unidev.com.br/phpbb3/styles/theme/main.css"
    target = canonical_url("http://unidev.com.br/phpbb3/styles/theme/nested.css")

    value = preserve_stylesheet(
        b'@import url("nested.css");',
        source,
        PurePosixPath("recursos/main/main.css"),
        {target: PurePosixPath("recursos/nested/nested.css")},
    )

    assert '@import url("../nested/nested.css")' in value
    assert 'url("")' not in value


def test_community_period_filter_keeps_header_dated_historical_post() -> None:
    source = "http://forum.unidev.com.br/forums/thread/37.aspx"
    raw = b"""
    <html><body>
      <div class="ForumPostArea">
        <h4 class="ForumPostHeader"><a name="37"></a>12-28-2006, 3:36 PM</h4>
        <div class="ForumPostContentText">Mensagem historica.</div>
      </div>
      <div class="ForumPostArea">
        <h4 class="ForumPostHeader"><a name="99"></a>12-28-2014, 3:36 PM</h4>
        <div class="ForumPostContentText">Mensagem futura.</div>
      </div>
    </body></html>
    """

    value = preserve_document(
        raw,
        source,
        PurePosixPath("comunidade/topicos/37/index.html"),
        RouteRegistry.from_urls((source,), "20070101000000"),
        {},
        capture_timestamp="20070101000000",
        period_start="2000-01-01T00:00:00",
        period_end="2013-03-30T11:18:00",
    )

    assert "Mensagem historica." in value
    assert "Mensagem futura." not in value


def test_preserves_historical_document_while_making_it_local_and_inert() -> None:
    source_url = "http://unidev.com.br/phpbb3/viewtopic.php?t=49617"
    forum_url = "http://unidev.com.br/phpbb3/viewforum.php?f=19"
    raw = """<!doctype html><html><head>
      <meta http-equiv="Content-Type" content="text/html; charset=windows-1252">
      <meta http-equiv="refresh" content="1;url=https://web.archive.org/">
      <base href="http://unidev.com.br/phpbb3/">
      <link rel="stylesheet" href="styles/prosilver/theme/stylesheet.css">
      <link id="alternate" rel="alternate" href="feed/rss.php">
      <script>alert(1)</script></head><body onload="alert(2)">
      <table class="tablebg"><tr><td>Fórum de programação</td></tr></table>
      <a id="forum" href="viewforum.php?f=19">Precisa-se</a>
      <a id="topic" href="https://web.archive.org/web/20110101000000/http://unidev.com.br/phpbb3/viewtopic.php?t=49617#p342938">Tópico</a>
      <a id="missing" href="viewtopic.php?t=99999">Ausente</a>
      <a id="external" href="https://example.org/">Externo</a>
      <a id="wayback-case" href="https://WEB.ARCHIVE.ORG/">Wayback</a>
      <img id="logo" src="styles/prosilver/imageset/site_logo.gif" onerror="alert(3)">
      <img id="missing-image" src="https://example.org/missing.gif">
      <form action="ucp.php?mode=login" method="post"><input name="username"><button>Entrar</button></form>
      </body></html>""".encode("windows-1252")
    registry = RouteRegistry.from_urls((source_url, forum_url))
    resources = {
        canonical_url(
            "http://unidev.com.br/phpbb3/styles/prosilver/theme/stylesheet.css"
        ): PurePosixPath("media/css/theme.css"),
        canonical_url(
            "http://unidev.com.br/phpbb3/styles/prosilver/imageset/site_logo.gif"
        ): PurePosixPath("media/images/site_logo.gif"),
    }

    preserved = preserve_document(
        raw,
        source_url,
        PurePosixPath("phpbb3/topicos/49617/index.html"),
        registry,
        resources,
    )

    document = html.document_fromstring(preserved)
    assert document.xpath("//table[@class='tablebg']")[0].text_content() == "Fórum de programação"
    assert document.xpath("//script|//base|//link[@id='alternate']") == []
    assert document.xpath("//meta[translate(@http-equiv, 'REFSH', 'refsh')='refresh']") == []
    assert len(document.xpath("//meta[@charset='utf-8']")) == 1
    assert document.xpath("//meta[@http-equiv='Content-Security-Policy']")
    assert document.get("onload") is None
    assert document.get_element_by_id("forum").get("href") == "../../foruns/19/index.html"
    assert document.get_element_by_id("topic").get("href") == "index.html#p342938"
    assert document.get_element_by_id("missing").get("href") is None
    assert "archive-link-missing" in document.get_element_by_id("missing").get("class")
    assert document.get_element_by_id("external").get("href") is None
    assert "archive-link-missing" in document.get_element_by_id("external").get("class")
    assert document.get_element_by_id("wayback-case").get("href") is None
    assert document.get_element_by_id("logo").get("src") == "../../../media/images/site_logo.gif"
    assert document.get_element_by_id("logo").get("onerror") is None
    assert document.get_element_by_id("missing-image").get("src") is None
    form = document.xpath("//form")[0]
    assert form.get("action") is None
    assert form.get("aria-disabled") == "true"
    assert all(
        control.get("disabled") == "disabled" for control in form.xpath(".//input|.//button")
    )
    assert "web.archive.org" not in preserved
    assert "charset=windows-1252" not in preserved
