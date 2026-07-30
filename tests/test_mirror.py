# pyright: reportMissingImports=false
from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from unidev_archive.mirror import (
    MirrorIntegrityError,
    _enable_search_page,
    _load_resources,
    _nearest_candidate,
    _resource_name,
    _resource_target,
    _select_pages,
    _validate_output,
)
from unidev_archive.routing import RouteRegistry


def test_search_asset_derives_deployment_base_from_its_own_url() -> None:
    script = (Path(__file__).parents[1] / "src/unidev_archive/static/search.js").read_text(
        encoding="utf-8"
    )

    assert 'new URL("../", import.meta.url).pathname' in script
    assert 'baseUrl: "/unidev/"' not in script


def test_enables_pagefind_only_on_preserved_search_page() -> None:
    value = """<html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'"></head><body><p>Busca original</p></body></html>"""

    result = _enable_search_page(value, PurePosixPath("busca/index.html"))

    assert 'id="search-form"' in result
    assert "Busca geral no acervo UniDev" in result
    assert 'placeholder="Digite o que deseja encontrar"' in result
    assert 'src="../assets/search.js"' in result
    assert "script-src 'self' 'wasm-unsafe-eval'" in result
    assert "Busca original" in result


def test_resource_alias_candidates_are_sorted_before_temporal_bisect() -> None:
    requested = "http://cdn.example/avatar.gif"
    rows = (
        {
            "capture_id": 2,
            "canonical_url": "http://live.example/avatar.gif",
            "original_url": "http://live.example/avatar.gif",
            "requested_url": requested,
            "timestamp": "20260729002740",
            "raw_sha256": "b" * 64,
            "relative_path": "blobs/b",
            "byte_length": 1,
            "mimetype": "image/gif",
        },
        {
            "capture_id": 1,
            "canonical_url": "http://archive.example/avatar.gif",
            "original_url": "http://archive.example/avatar.gif",
            "requested_url": requested,
            "timestamp": "20070116175655",
            "raw_sha256": "a" * 64,
            "relative_path": "blobs/a",
            "byte_length": 1,
            "mimetype": "image/gif",
        },
    )

    class Connection:
        def execute(
            self, query: str, parameters: tuple[object, ...]
        ) -> tuple[dict[str, object], ...]:
            return () if "resource_references" in query else rows

    class Database:
        connection = Connection()

    candidates, _, _ = _load_resources(Database())  # type: ignore[arg-type]
    choices = candidates[requested]

    assert [choice.timestamp for choice in choices] == ["20070116175655", "20260729002740"]
    assert _nearest_candidate(choices, "20060711103128").capture_id == 1


def test_discarded_duplicate_page_remains_a_resolvable_alias() -> None:
    rows = (
        {
            "capture_id": 1,
            "original_url": "http://unidev.com.br/phpbb3/viewtopic.php?f=1&t=42",
            "requested_url": None,
            "timestamp": "20100101000000",
            "raw_sha256": "a" * 64,
            "relative_path": "blobs/a",
            "byte_length": 1,
            "mimetype": "text/html",
        },
        {
            "capture_id": 2,
            "original_url": "http://unidev.com.br/phpbb3/viewtopic.php?f=2&t=42",
            "requested_url": None,
            "timestamp": "20110101000000",
            "raw_sha256": "b" * 64,
            "relative_path": "blobs/b",
            "byte_length": 1,
            "mimetype": "text/html",
        },
    )

    class Connection:
        def execute(
            self, query: str, parameters: tuple[object, ...]
        ) -> tuple[dict[str, object], ...]:
            return () if "GROUP BY ps.capture_id" in query else rows

    class Database:
        connection = Connection()

    pages, duplicates, aliases = _select_pages(Database())  # type: ignore[arg-type]
    registry = RouteRegistry.from_mapped_entries(
        (alias.original_url, alias.timestamp, alias.route) for alias in aliases
    )

    assert len(pages) == 1
    assert duplicates == 1
    assert len(aliases) == 2
    assert registry.resolve(rows[0]["original_url"], rows[0]["timestamp"]) is not None
    assert registry.resolve(rows[1]["original_url"], rows[1]["timestamp"]) is not None


def test_wayback_hostname_as_plain_text_does_not_fail_link_validator(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        "<html><body><p>Discussão histórica: web.archive.org</p></body></html>",
        encoding="utf-8",
    )

    _validate_output(tmp_path)


def test_validator_neutralizes_uncaptured_local_fragments(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<html><body><a id="cross" href="target.html#missing">post</a><a id="same" href="#missing">seção</a></body></html>',
        encoding="utf-8",
    )
    (tmp_path / "target.html").write_text(
        '<html><body><p id="preserved">conteúdo</p></body></html>',
        encoding="utf-8",
    )

    _validate_output(tmp_path)

    published = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'href="target.html"' in published
    assert "target.html#missing" not in published
    assert 'id="same"' in published
    assert "archive-link-missing" in published


def test_validator_rejects_broken_active_search_action(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<html><body><form action="busca/index.html"><input name="q"></form></body></html>',
        encoding="utf-8",
    )

    with pytest.raises(MirrorIntegrityError, match="link local quebrado"):
        _validate_output(tmp_path)


def test_validator_rejects_active_external_anchor(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<html><body><a href="https://example.org/">externo</a></body></html>',
        encoding="utf-8",
    )

    with pytest.raises(MirrorIntegrityError, match="link externo ativo"):
        _validate_output(tmp_path)


def test_validator_rejects_external_xlink_href_in_svg(tmp_path: Path) -> None:
    (tmp_path / "unsafe.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><image xlink:href="https://attacker.example/pixel.png"/></svg>',
        encoding="utf-8",
    )

    with pytest.raises(MirrorIntegrityError, match="atributo SVG ativo"):
        _validate_output(tmp_path)


def test_same_css_payload_at_different_bases_has_distinct_output_target() -> None:
    sha256 = "a" * 64

    first = _resource_target(sha256, "http://unidev.com.br/a/main.css", "text/css")
    second = _resource_target(sha256, "http://unidev.com.br/b/main.css", "text/css")

    assert first != second


def test_resource_name_keeps_safe_mime_suffix_after_length_limit() -> None:
    name = _resource_name(
        "http://unidev.com.br/phpbb3/image.php/" + "x" * 220 + ".php",
        "image/svg+xml",
    )

    assert len(name) <= 160
    assert name.endswith(".svg")
