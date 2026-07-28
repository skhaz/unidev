# pyright: reportMissingImports=false
from __future__ import annotations

from unidev_archive.parser import parse_page


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
