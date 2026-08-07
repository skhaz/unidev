# pyright: reportMissingImports=false
from __future__ import annotations

from unidev_archive.parser import parse_css_references, parse_page


def test_extracts_all_historical_css_reference_forms() -> None:
    references = parse_css_references(
        b"@import url(\"nested.css\");@import 'print.css';a{background:url(images/a.gif)}",
        "http://unidev.com.br/phpbb3/styles/main.css",
    )

    assert set(references) == {
        "http://unidev.com.br/phpbb3/styles/images/a.gif",
        "http://unidev.com.br/phpbb3/styles/nested.css",
        "http://unidev.com.br/phpbb3/styles/print.css",
    }


def test_ignores_network_references_from_elements_removed_during_preservation() -> None:
    page = parse_page(
        b"""
        <iframe src="../banner.asp"></iframe>
        <script src="legacy.js"></script>
        <object data="movie.swf"><embed src="movie.swf"></embed></object>
        <svg><image href="https://example.org/pixel.png"/></svg>
        <link rel="alternate" href="feed/rss.php">
        <img src="images/logo.gif">
        """,
        "http://unidev.com.br/phpbb3/index.php",
    )

    assert page.references == ("http://unidev.com.br/phpbb3/images/logo.gif",)
    assert page.asset_references == page.references


def test_srcset_data_url_comma_does_not_create_phantom_reference() -> None:
    page = parse_page(
        b'<img srcset="data:image/png;base64,AAAA 1x">',
        "http://unidev.com.br/phpbb3/index.php",
    )

    assert page.references == ()


def test_ignores_css_references_inside_comments_and_strings() -> None:
    references = parse_css_references(
        b'/* url(comment.gif) */a::before{content:"url(string.gif)"}',
        "http://unidev.com.br/phpbb3/styles/main.css",
    )

    assert references == ()


def test_unwraps_wayback_reference_before_internal_classification() -> None:
    page = parse_page(
        b'<a href="https://WEB.ARCHIVE.ORG/web/20110101000000/http://unidev.com.br/phpbb3/viewtopic.php?t=99999">T</a>',
        "http://unidev.com.br/phpbb3/index.php",
    )

    assert page.references == ("http://unidev.com.br/phpbb3/viewtopic.php?t=99999",)


def test_accepts_xml_declaration_in_decoded_html() -> None:
    page = parse_page(
        b'<?xml version="1.0" encoding="iso-8859-1"?><html><title>UniDev</title></html>',
        "http://unidev.com.br/phpbb3/index.php",
    )

    assert page.era == "phpbb3"
    assert page.posts == ()


def test_accepts_archived_empty_forum_responses() -> None:
    page = parse_page(
        b"",
        "http://unidev.com.br/phpbb3/portal/syndicate_downloads.php",
    )

    assert page.era == "phpbb3"
    assert page.posts == ()
    assert page.references == ()


def test_extracts_snitz_post_and_relative_assets() -> None:
    raw = """
    <meta charset="ISO-8859-1"><title>Forum Unidev</title>
    <link rel="stylesheet" href="imagens/forum.css">
    <a href="FORUM.asp?FORUM_ID=12">SDL</a>
    <a href="post.asp?Topic_Title=Engine%20em%20C">Responder</a>
    <table><tr>
      <td><a href="pop_profile.asp?mode=display&id=16263"><b>skhaz</b></a></td>
      <td><a name="266040"></a>Postado - 24/05/2007 : 19:24:00
          <hr><font>Programação em C<br><img src="imagens/c.gif">funciona.</font></td>
    </tr></table>
    """.encode("cp1252")

    page = parse_page(
        raw,
        "http://www.unidev.com.br/forum/topic.asp?TOPIC_ID=38915&FORUM_ID=12",
    )

    assert page.topic_title == "Engine em C"
    assert page.forum_name == "SDL"
    assert page.source_encoding == "windows-1252"
    assert len(page.posts) == 1
    post = page.posts[0]
    assert post.post_id == 266040
    assert post.author_id == 16263
    assert post.author_name == "skhaz"
    assert post.posted_at == "2007-05-24T19:24:00"
    assert post.body_text == "Programação em C\nfunciona."
    assert "Postado" not in post.body_html
    assert "http://unidev.com.br/forum/imagens/forum.css" in page.references
    assert "http://unidev.com.br/forum/imagens/c.gif" in page.references


def test_extracts_snitz_forum_listing() -> None:
    raw = """
    <table><tr>
      <td><a href="topic.asp?TOPIC_ID=34060"><img></a></td>
      <td><a href="topic.asp?TOPIC_ID=34060">Duelo: Black.Lord x SKHAZ</a></td>
      <td>Black.Lord</td><td>44</td><td>943</td>
      <td>16/12/2006 20:08:15<br>por:
        <a href="pop_profile.asp?mode=display&id=16263">skhaz</a>
      </td>
    </tr></table>
    """.encode("cp1252")

    page = parse_page(raw, "http://www.unidev.com.br/forum/forum.asp?FORUM_ID=12")

    assert len(page.listings) == 1
    listing = page.listings[0]
    assert listing.topic_id == 34060
    assert listing.forum_id == 12
    assert listing.title == "Duelo: Black.Lord x SKHAZ"
    assert listing.author_name == "Black.Lord"
    assert listing.last_author_id == 16263
    assert listing.last_author_name == "skhaz"
    assert listing.last_posted_at == "2006-12-16T20:08:15"


def test_extracts_phpbb2_post() -> None:
    raw = """
    <meta charset="iso-8859-1"><title>UniDev :: Exibir tópico - DirectX no GCC</title>
    <link rel="up" href="viewforum.php?f=9" title="DirectX">
    <table><tr><td class="toprow"><b>DirectX no GCC</b></td></tr></table>
    <table>
      <tr><td><span class="largetext"><a name="264891"></a><b>skhaz</b></span></td>
          <td><span class="largetext">Como usar DirectX no GCC?<script>alert(1)</script></span></td></tr>
      <tr><td>Qua Mar 28, 2007 6:23 pm</td>
          <td><a href="profile.php?mode=viewprofile&u=16263">perfil</a></td></tr>
    </table>
    """.encode("cp1252")

    page = parse_page(
        raw,
        "http://forum.unidev.com.br/phpbb2/viewtopic.php?f=9&t=37733&sid=abc",
    )

    assert page.topic_id == 37733
    assert page.forum_id == 9
    assert page.topic_title == "DirectX no GCC"
    assert page.forum_name == "DirectX"
    assert page.posts[0].post_id == 264891
    assert page.posts[0].author_id == 16263
    assert page.posts[0].posted_at == "2007-03-28T18:23:00"
    assert page.posts[0].body_text == "Como usar DirectX no GCC?"
    assert "script" not in page.posts[0].body_html


def test_uses_rendered_topic_identity_for_previous_and_next_views() -> None:
    raw = """
    <meta charset="iso-8859-1"><title>UniDev :: Exibir tópico - Real</title>
    <link rel="up" href="viewforum.php?f=5" title="Geral">
    <a href="viewtopic.php?t=7574&view=previous">Anterior</a>
    <a href="viewtopic.php?t=7574&start=15">2</a>
    <table><tr><td class="toprow"><b>Real</b></td></tr></table>
    <table><tr>
      <td><a name="31413"></a><b>autor</b></td>
      <td><span class="largetext">Mensagem real.</span></td>
    </tr></table>
    """.encode()

    page = parse_page(
        raw,
        "http://forum.unidev.com.br/phpbb2/viewtopic.php?t=35996&view=previous",
    )

    assert page.topic_id == 7574
    assert page.forum_id == 5
    assert page.posts[0].topic_id == 7574


def test_parses_community_server_posts_and_ids() -> None:
    page = parse_page(
        b"""
        <html><head><title>Forum Unidev - Manutencao do forum</title></head><body>
        <div class="CommonBreadCrumbArea"><a href="/forums/5/ShowForum.aspx" title="Assuntos diversos">Assuntos diversos</a></div>
        <div class="ForumPostArea">
          <h4 class="ForumPostHeader"><a name="37"></a> 12-28-2006, 3:36 PM</h4>
          <li class="ForumPostUserName"><a href="/members/rock.aspx">rock</a></li>
          <img src="/users/avatar.aspx?userid=2107">
          <div class="ForumPostContentText"><p>Primeira mensagem</p></div>
        </div>
        <div class="ForumPostArea">
          <h4 class="ForumPostHeader"><a name="38"></a> 12-28-2006, 3:40 PM</h4>
          <li class="ForumPostUserName"><a href="/members/admin.aspx">admin</a></li>
          <a href="/search/SearchResults.aspx?u=1&o=DateDescending">Posts</a>
          <div class="ForumPostContentText"><p>Resposta</p></div>
        </div>
        </body></html>
        """,
        "http://forum.unidev.com.br/forums/thread/37.aspx",
        "community-server",
    )

    assert page.era == "community-server"
    assert page.topic_id == 37
    assert page.forum_id == 5
    assert page.forum_name == "Assuntos diversos"
    assert [post.post_id for post in page.posts] == [37, 38]
    assert [post.author_id for post in page.posts] == [2107, 1]
    assert page.posts[0].posted_at == "2006-12-28T15:36:00"
    assert page.posts[1].body_text == "Resposta"
    direct = parse_page(
        b"<html><title>Forum</title></html>",
        "http://forum.unidev.com.br/forums/37/ShowThread.aspx",
        "community-server",
    )
    assert direct.topic_id == 37


def test_classifies_dynamic_external_image_as_asset_by_html_context() -> None:
    page = parse_page(
        b'<img src="https://cdn.example/avatar.php?u=1">',
        "http://unidev.com.br/phpbb3/viewtopic.php?t=1",
    )

    assert page.asset_references == ("http://cdn.example/avatar.php?u=1",)


def test_extracts_phpbb3_portal_news_as_real_post() -> None:
    raw = """
    <table class="tablebg">
      <tr><td class="cat"><h4><a href="viewtopic.php?p=385810#p385810"><strong>Unity e Nintendo</strong></a></h4></td></tr>
      <tr><td class="row2"><a href="memberlist.php?mode=viewprofile&u=18927">mcunha98</a>
        <a href="viewforum.php?f=81">Notícias</a><span>30 Mar 2013, 11:18</span></td></tr>
      <tr><td class="row1"><table><tr><td class="genmed"><div style="margin:5px">Conteúdo completo da notícia.</div></td></tr></table></td></tr>
      <tr><td><a href="posting.php?mode=reply&f=81&t=55715">Write comments</a></td></tr>
    </table>
    """.encode()

    page = parse_page(raw, "http://unidev.com.br/phpbb3/portal.php")

    post = page.posts[0]
    assert post.topic_id == 55715
    assert post.forum_id == 81
    assert post.post_id == 385810
    assert post.author_id == 18927
    assert post.author_name == "mcunha98"
    assert post.posted_at == "2013-03-30T11:18:00"
    assert post.body_text == "Conteúdo completo da notícia."


def test_extracts_phpbb3_normal_post() -> None:
    raw = """
    <meta charset="utf-8"><title>UniDev - Programação de Jogos</title>
    <link rel="up" href="viewforum.php?f=19" title="Precisa-se">
    <div id="pageheader"><h2>Vaga Programador - Curitiba</h2></div>
    <table class="tablebg">
      <tr><td><a name="p342938"></a><b class="postauthor">skhaz</b></td>
          <td><b>Posted:</b> Sat Sep 12, 2009 10:25 pm</td></tr>
      <tr><td class="profile"></td><td><div class="postbody">Tenho interesse.<br>Enviei MP.</div></td></tr>
      <tr><td></td><td><a href="memberlist.php?mode=viewprofile&u=16263">perfil</a></td></tr>
    </table>
    """.encode()

    page = parse_page(raw, "http://unidev.com.br/phpbb3/viewtopic.php?f=19&t=49617")

    post = page.posts[0]
    assert page.topic_title == "Vaga Programador - Curitiba"
    assert page.forum_name == "Precisa-se"
    assert post.post_id == 342938
    assert post.author_id == 16263
    assert post.posted_at == "2009-09-12T22:25:00"
    assert post.body_text == "Tenho interesse.\nEnviei MP."


def test_extracts_phpbb3_forum_listing_activity() -> None:
    raw = """
    <meta charset="utf-8"><title>UniDev • View forum - Precisa-se</title>
    <table><tr>
      <td></td>
      <td><a title="Enviado: 11 Set 2009, 19:35" href="viewtopic.php?f=19&t=49617" class="topictitle">Vaga Programador - Curitiba</a></td>
      <td><a href="memberlist.php?mode=viewprofile&u=50739">Make_Wish</a></td>
      <td>2</td><td>152</td>
      <td><p>12 Set 2009, 22:25</p><a href="memberlist.php?mode=viewprofile&u=16263"><b>skhaz</b></a><a href="viewtopic.php?f=19&t=49617&p=342938#p342938">última</a></td>
    </tr></table>
    """.encode()

    page = parse_page(raw, "http://unidev.com.br/phpbb3/viewforum.php?f=19")

    assert len(page.posts) == 0
    assert len(page.listings) == 1
    listing = page.listings[0]
    assert listing.topic_id == 49617
    assert listing.title == "Vaga Programador - Curitiba"
    assert listing.author_id == 50739
    assert listing.created_at == "2009-09-11T19:35:00"
    assert listing.last_post_id == 342938
    assert listing.last_author_id == 16263
    assert listing.last_author_name == "skhaz"
    assert listing.last_posted_at == "2009-09-12T22:25:00"


def test_extracts_phpbb3_print_view_without_inventing_post_id() -> None:
    raw = """
    <meta charset="utf-8"><div id="page-header"><h2>Tópico antigo</h2></div>
    <div id="page-body"><div class="post">
      <h3>Tópico antigo</h3>
      <div class="date">Enviado: <strong>01 Dez 2003, 13:16</strong></div>
      <div class="author">por <strong>Osmar</strong></div>
      <div class="content">Mensagem preservada.</div>
    </div></div>
    """.encode()

    page = parse_page(
        raw,
        "http://unidev.com.br/phpbb3/viewtopic.php?f=10&t=5077&view=print",
    )

    assert page.topic_title == "Tópico antigo"
    assert page.posts[0].post_id is None
    assert page.posts[0].posted_at == "2003-12-01T13:16:00"
    assert page.posts[0].body_text == "Mensagem preservada."
