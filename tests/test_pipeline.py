# pyright: reportMissingImports=false
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

import pytest

from unidev_archive.mirror import MirrorIntegrityError
from unidev_archive.pipeline import _load_manifest, rebuild_archive


def _cdx_digest(content: bytes) -> str:
    return (
        base64.b32encode(hashlib.sha1(content, usedforsecurity=False).digest()).decode().rstrip("=")
    )


def _write_period(archive: Path) -> None:
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "period.json").write_text(
        json.dumps(
            {
                "start": "2000-01-01T00:00:00",
                "end": "2013-03-30T11:18:00",
                "evidence": {
                    "post_id": 385810,
                    "posted_at": "2013-03-30T11:18:00",
                    "capture_timestamp": "20130527024512",
                    "original_url": "http://unidev.com.br/phpbb3/portal.php",
                    "digest": "fixture-digest",
                },
            }
        ),
        encoding="utf-8",
    )


def test_manifest_rejects_blob_path_escape(tmp_path: Path) -> None:
    manifest = tmp_path / "captures.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "timestamp": "20110111000000",
                "original_url": "http://unidev.com.br/phpbb3/",
                "statuscode": 200,
                "mimetype": "text/html",
                "digest": "A" * 32,
                "payload_digest": "A" * 32,
                "cdx_digest_matches_payload": True,
                "source": "wayback",
                "length": 10,
                "retrieved_length": 10,
                "sha256": "a" * 64,
                "path": "../outside",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="registro inválido"):
        _load_manifest(manifest)


def test_manifest_rejects_duplicate_capture_identity(tmp_path: Path) -> None:
    raw = b"<title>UniDev</title>"
    sha256 = hashlib.sha256(raw).hexdigest()
    row = {
        "timestamp": "20110111000000",
        "original_url": "http://unidev.com.br/phpbb3/index.php",
        "statuscode": 200,
        "digest": _cdx_digest(raw),
        "payload_digest": _cdx_digest(raw),
        "cdx_digest_matches_payload": True,
        "source": "wayback",
        "mimetype": "text/html",
        "length": len(raw),
        "retrieved_length": len(raw),
        "sha256": sha256,
        "path": f"blobs/{sha256[:2]}/{sha256}",
    }
    manifest = tmp_path / "captures.jsonl"
    manifest.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="captura duplicada"):
        _load_manifest(manifest)


@pytest.mark.parametrize(
    ("source", "digest_matches"),
    (("live-origin", True), ("wayback", False), ("commoncrawl", False)),
)
def test_manifest_rejects_unpublishable_capture_sources(
    tmp_path: Path,
    source: str,
    digest_matches: bool,
) -> None:
    raw = b"verified payload"
    sha256 = hashlib.sha256(raw).hexdigest()
    row = {
        "timestamp": "20110111000000",
        "original_url": "http://unidev.com.br/phpbb3/index.php",
        "statuscode": 200,
        "digest": _cdx_digest(raw),
        "payload_digest": _cdx_digest(raw),
        "cdx_digest_matches_payload": digest_matches,
        "source": source,
        "mimetype": "text/html",
        "length": len(raw),
        "retrieved_length": len(raw),
        "sha256": sha256,
        "path": f"blobs/{sha256[:2]}/{sha256}",
    }
    manifest = tmp_path / "captures.jsonl"
    manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="registro inválido"):
        _load_manifest(manifest)


def test_rebuild_rejects_false_cdx_digest_attestation(tmp_path: Path) -> None:
    raw = b"<title>UniDev</title>"
    sha256 = hashlib.sha256(raw).hexdigest()
    archive = tmp_path / "archive"
    _write_period(archive)
    blob = archive / "blobs" / sha256[:2] / sha256
    blob.parent.mkdir(parents=True)
    blob.write_bytes(raw)
    row = {
        "timestamp": "20110111000000",
        "original_url": "http://unidev.com.br/phpbb3/index.php",
        "statuscode": 200,
        "digest": "A" * 32,
        "payload_digest": _cdx_digest(raw),
        "cdx_digest_matches_payload": True,
        "source": "wayback",
        "mimetype": "text/html",
        "length": len(raw),
        "retrieved_length": len(raw),
        "sha256": sha256,
        "path": f"blobs/{sha256[:2]}/{sha256}",
    }
    manifest = archive / "captures.jsonl"
    manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="atestado de digest inválido"):
        rebuild_archive(
            manifest,
            tmp_path / "archive.sqlite3",
            tmp_path / "dist",
            verify_period_evidence=False,
        )


def test_rebuild_includes_messages_after_2009_until_forum_end(tmp_path: Path) -> None:
    raw = b"""
    <meta charset="utf-8"><title>UniDev</title>
    <link rel="up" href="viewforum.php?f=19" title="Comunidade">
    <div id="pageheader"><h2>Atividade posterior</h2></div>
    <table class="tablebg">
      <tr><td><a name="p400000"></a><b class="postauthor">usuario</b></td>
          <td><b>Posted:</b> Mon Jan 10, 2011 8:30 pm</td></tr>
      <tr><td class="profile"></td><td><div class="postbody">Mensagem preservada.</div></td></tr>
    </table>
    <table class="tablebg">
      <tr><td><a name="p500000"></a><b class="postauthor">usuario</b></td>
          <td><b>Posted:</b> Fri Jan 10, 2014 8:30 pm</td></tr>
      <tr><td class="profile"></td><td><div class="postbody">Fora do periodo.</div></td></tr>
    </table>
    """
    forum_raw = b'<meta charset="utf-8"><title>Comunidade</title><body>Forum</body>'
    archive = tmp_path / "archive"
    _write_period(archive)
    records = []
    for original_url, content in (
        ("http://unidev.com.br/phpbb3/viewtopic.php?f=19&t=60000", raw),
        ("http://unidev.com.br/phpbb3/viewforum.php?f=19", forum_raw),
        ("http://unidev.com.br/phpbb3/index.php", forum_raw),
    ):
        sha256 = hashlib.sha256(content).hexdigest()
        blob = archive / "blobs" / sha256[:2] / sha256
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(content)
        records.append(
            {
                "timestamp": "20110111000000",
                "original_url": original_url,
                "statuscode": 200,
                "digest": _cdx_digest(content),
                "payload_digest": _cdx_digest(content),
                "cdx_digest_matches_payload": True,
                "source": "wayback",
                "mimetype": "text/html",
                "length": len(content),
                "retrieved_length": len(content),
                "sha256": sha256,
                "path": f"blobs/{sha256[:2]}/{sha256}",
            }
        )
    manifest = archive / "captures.jsonl"
    manifest.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    stats = rebuild_archive(
        manifest,
        tmp_path / "archive.sqlite3",
        tmp_path / "dist",
        verify_period_evidence=False,
    )

    assert stats.extracted_posts == 1
    assert stats.site.posts == 1
    topic = tmp_path / "dist" / "phpbb3" / "topicos" / "60000" / "index.html"
    assert topic.is_file()
    published_topic = topic.read_text(encoding="utf-8")
    assert "Mensagem preservada." in published_topic
    assert "Fora do periodo." not in published_topic
    assert not (tmp_path / "dist" / "topicos" / "60000.html").exists()


def test_unparsed_community_server_page_still_blocks_broken_links(tmp_path: Path) -> None:
    raw = b'<a href="/forums/thread/37.aspx">Thread</a><img src="/forums/theme/logo.gif">'
    sha256 = hashlib.sha256(raw).hexdigest()
    archive = tmp_path / "archive"
    _write_period(archive)
    blob = archive / "blobs" / sha256[:2] / sha256
    blob.parent.mkdir(parents=True)
    blob.write_bytes(raw)
    record = {
        "timestamp": "20050101000000",
        "original_url": "http://forum.unidev.com.br/forums/default.aspx",
        "statuscode": 200,
        "digest": _cdx_digest(raw),
        "payload_digest": _cdx_digest(raw),
        "cdx_digest_matches_payload": True,
        "source": "wayback",
        "mimetype": "text/html",
        "length": len(raw),
        "retrieved_length": len(raw),
        "sha256": sha256,
        "path": f"blobs/{sha256[:2]}/{sha256}",
    }
    manifest = archive / "captures.jsonl"
    manifest.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(MirrorIntegrityError, match=r"1 página.*1 recurso"):
        rebuild_archive(
            manifest,
            tmp_path / "archive.sqlite3",
            tmp_path / "dist",
            verify_period_evidence=False,
        )


@pytest.mark.parametrize(
    "href",
    (
        "viewtopic.php?f=19&t=99999",
        "/forum/topic/99999",
        "https://WEB.ARCHIVE.ORG/web/20110101000000/http://unidev.com.br/phpbb3/viewtopic.php?t=99999",
    ),
)
def test_rebuild_refuses_to_publish_a_broken_internal_topic_link(tmp_path: Path, href: str) -> None:
    raw = f'<title>Forum</title><a href="{href}">Topico real</a>'.encode()
    sha256 = hashlib.sha256(raw).hexdigest()
    archive = tmp_path / "archive"
    _write_period(archive)
    blob = archive / "blobs" / sha256[:2] / sha256
    blob.parent.mkdir(parents=True)
    blob.write_bytes(raw)
    record = {
        "timestamp": "20110111000000",
        "original_url": "http://unidev.com.br/phpbb3/index.php",
        "statuscode": 200,
        "digest": _cdx_digest(raw),
        "payload_digest": _cdx_digest(raw),
        "cdx_digest_matches_payload": True,
        "source": "wayback",
        "mimetype": "text/html",
        "length": len(raw),
        "retrieved_length": len(raw),
        "sha256": sha256,
        "path": f"blobs/{sha256[:2]}/{sha256}",
    }
    manifest = archive / "captures.jsonl"
    manifest.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(MirrorIntegrityError, match="1 página"):
        rebuild_archive(
            manifest,
            tmp_path / "archive.sqlite3",
            tmp_path / "dist",
            verify_period_evidence=False,
        )

    assert not (tmp_path / "dist" / "index.html").exists()


def test_rebuild_makes_uncaptured_write_action_inert(tmp_path: Path) -> None:
    raw = b'<title>Forum</title><a id="reply" href="posting.php?mode=reply&t=42">Responder</a>'
    sha256 = hashlib.sha256(raw).hexdigest()
    archive = tmp_path / "archive"
    _write_period(archive)
    blob = archive / "blobs" / sha256[:2] / sha256
    blob.parent.mkdir(parents=True)
    blob.write_bytes(raw)
    record = {
        "timestamp": "20110111000000",
        "original_url": "http://unidev.com.br/phpbb3/index.php",
        "statuscode": 200,
        "digest": _cdx_digest(raw),
        "payload_digest": _cdx_digest(raw),
        "cdx_digest_matches_payload": True,
        "source": "wayback",
        "mimetype": "text/html",
        "length": len(raw),
        "retrieved_length": len(raw),
        "sha256": sha256,
        "path": f"blobs/{sha256[:2]}/{sha256}",
    }
    manifest = archive / "captures.jsonl"
    manifest.write_text(json.dumps(record) + "\n", encoding="utf-8")

    rebuild_archive(
        manifest,
        tmp_path / "archive.sqlite3",
        tmp_path / "dist",
        verify_period_evidence=False,
    )

    homepage = (tmp_path / "dist" / "index.html").read_text(encoding="utf-8")
    assert 'id="reply"' in homepage
    assert "archive-link-missing" in homepage
    assert 'href="posting.php' not in homepage


def test_binary_capture_cannot_satisfy_required_page_link(tmp_path: Path) -> None:
    base = "http://unidev.com.br/phpbb3/"
    payloads = (
        (base + "index.php", "text/html", b'<a href="avatar.php">Pagina</a>'),
        (base + "avatar.php", "image/gif", b"GIF89a"),
    )
    archive = tmp_path / "archive"
    _write_period(archive)
    rows = []
    for original_url, mimetype, content in payloads:
        sha256 = hashlib.sha256(content).hexdigest()
        blob = archive / "blobs" / sha256[:2] / sha256
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(content)
        rows.append(
            {
                "timestamp": "20110111000000",
                "original_url": original_url,
                "statuscode": 200,
                "digest": _cdx_digest(content),
                "payload_digest": _cdx_digest(content),
                "cdx_digest_matches_payload": True,
                "source": "wayback",
                "mimetype": mimetype,
                "length": len(content),
                "retrieved_length": len(content),
                "sha256": sha256,
                "path": f"blobs/{sha256[:2]}/{sha256}",
            }
        )
    manifest = archive / "captures.jsonl"
    manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    with pytest.raises(MirrorIntegrityError, match="1 página"):
        rebuild_archive(
            manifest,
            tmp_path / "archive.sqlite3",
            tmp_path / "dist",
            verify_period_evidence=False,
        )


def test_rebuild_resolves_verified_wayback_requested_url_alias(tmp_path: Path) -> None:
    requested_url = "http://unidev.com.br/IMAGES/Logo.GIF"
    payloads = (
        (
            "http://unidev.com.br/phpbb3/index.php",
            None,
            "text/html",
            f'<title>UniDev</title><img src="{requested_url}">'.encode(),
            "wayback",
        ),
        (
            "http://www.unidev.com.br:80/images/logo.gif",
            requested_url,
            "image/gif",
            b"GIF89a",
            "wayback-availability",
        ),
    )
    archive = tmp_path / "archive"
    _write_period(archive)
    rows = []
    for original_url, requested, mimetype, content, source in payloads:
        sha256 = hashlib.sha256(content).hexdigest()
        blob = archive / "blobs" / sha256[:2] / sha256
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(content)
        row = {
            "timestamp": "20130330000000",
            "original_url": original_url,
            "statuscode": 200,
            "digest": _cdx_digest(content),
            "payload_digest": _cdx_digest(content),
            "cdx_digest_matches_payload": True,
            "source": source,
            "mimetype": mimetype,
            "length": len(content),
            "retrieved_length": len(content),
            "sha256": sha256,
            "path": f"blobs/{sha256[:2]}/{sha256}",
        }
        if requested is not None:
            row["requested_url"] = requested
            row["digest_source"] = "computed-payload"
            row["cdx_digest_matches_payload"] = None
        rows.append(row)
    manifest = archive / "captures.jsonl"
    manifest.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    rebuild_archive(
        manifest,
        tmp_path / "archive.sqlite3",
        tmp_path / "dist",
        verify_period_evidence=False,
    )

    homepage = (tmp_path / "dist" / "index.html").read_text(encoding="utf-8")
    assert requested_url not in homepage
    assert "recursos/" in homepage


def test_rebuild_rewrites_nested_css_to_verified_local_resource(tmp_path: Path) -> None:
    base = "http://unidev.com.br/phpbb3/"
    payloads = (
        (
            base + "viewtopic.php?t=1",
            "text/html",
            b'<link rel="stylesheet" href="styles/theme.css"><p>Topico</p>',
        ),
        (base + "index.php", "text/html", b"<title>UniDev</title><p>Forum</p>"),
        (base + "styles/theme.css", "text/css", b"body{background:url( images/bg.gif)}"),
        (base + "styles/images/bg.gif", "image/gif", b"GIF89a"),
    )
    archive = tmp_path / "archive"
    _write_period(archive)
    rows = []
    for original_url, mimetype, content in payloads:
        sha256 = hashlib.sha256(content).hexdigest()
        blob = archive / "blobs" / sha256[:2] / sha256
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(content)
        rows.append(
            {
                "timestamp": "20130330000000",
                "original_url": original_url,
                "statuscode": 200,
                "digest": _cdx_digest(content),
                "payload_digest": _cdx_digest(content),
                "cdx_digest_matches_payload": True,
                "source": "wayback",
                "mimetype": mimetype,
                "length": len(content),
                "retrieved_length": len(content),
                "sha256": sha256,
                "path": f"blobs/{sha256[:2]}/{sha256}",
            }
        )
    manifest = archive / "captures.jsonl"
    manifest.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    rebuild_archive(
        manifest,
        tmp_path / "archive.sqlite3",
        tmp_path / "dist",
        verify_period_evidence=False,
    )

    topic = tmp_path / "dist" / "phpbb3" / "topicos" / "1" / "index.html"
    assert "recursos/" in topic.read_text(encoding="utf-8")
    stylesheets = list((tmp_path / "dist" / "recursos").rglob("*.css"))
    assert len(stylesheets) == 1
    css = stylesheets[0].read_text(encoding="utf-8")
    assert "http://" not in css and "web.archive.org" not in css
    reference = re.search(r'url\("([^"]+)"\)', css)
    assert reference is not None
    assert (stylesheets[0].parent / reference.group(1)).resolve().is_file()
