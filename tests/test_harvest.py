# pyright: reportMissingImports=false
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from unidev_archive.harvest import _compact_manifest, read_cdx_files, write_inventory
from unidev_archive.manifest import payload_digest


def test_resume_does_not_trust_a_manifest_with_corrupt_blob(tmp_path: Path) -> None:
    blob = tmp_path / "blobs" / "aa" / ("a" * 64)
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"corrupt")
    manifest = tmp_path / "captures.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "timestamp": "20100101000000",
                "original_url": "http://unidev.com.br/phpbb3/",
                "sha256": "a" * 64,
                "path": f"blobs/aa/{'a' * 64}",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert _compact_manifest(manifest) == set()
    assert manifest.read_text(encoding="utf-8") == ""


def test_resume_compacts_duplicate_rows_to_verified_blob(tmp_path: Path) -> None:
    raw = b"verified"
    sha256 = hashlib.sha256(raw).hexdigest()
    blob = tmp_path / "blobs" / sha256[:2] / sha256
    blob.parent.mkdir(parents=True)
    blob.write_bytes(raw)
    key = {
        "timestamp": "20100101000000",
        "original_url": "http://unidev.com.br/phpbb3/",
    }
    manifest = tmp_path / "captures.jsonl"
    manifest.write_text(
        json.dumps({**key, "sha256": "a" * 64, "path": f"blobs/aa/{'a' * 64}"})
        + "\n"
        + json.dumps(
            {
                **key,
                "statuscode": 200,
                "mimetype": "text/html",
                "digest": payload_digest(raw),
                "payload_digest": payload_digest(raw),
                "cdx_digest_matches_payload": True,
                "source": "wayback",
                "length": len(raw),
                "retrieved_length": len(raw),
                "sha256": sha256,
                "path": f"blobs/{sha256[:2]}/{sha256}",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert _compact_manifest(manifest) == {("20100101000000", "http://unidev.com.br/phpbb3/")}
    assert len(manifest.read_text(encoding="utf-8").splitlines()) == 1


def test_consolidates_cdx_exports_without_losing_distinct_captures(tmp_path: Path) -> None:
    header = ["timestamp", "original", "statuscode", "mimetype", "digest", "length"]
    first = [
        header,
        ["20070101000000", "http://unidev.com.br/forum/", "200", "text/html", "A" * 32, "10"],
        ["20080101000000", "http://unidev.com.br/forum/", "200", "text/html", "B" * 32, "12"],
    ]
    second = [
        header,
        first[2],
        ["20110101000000", "http://unidev.com.br/phpbb3/", "200", "text/html", "C" * 32, "15"],
    ]
    (tmp_path / "a.json").write_text(json.dumps(first), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(second), encoding="utf-8")
    (tmp_path / "broken.json").write_text("not json", encoding="utf-8")

    with pytest.raises(ValueError, match="exportação CDX inválida"):
        read_cdx_files(sorted(tmp_path.glob("*.json")))
    (tmp_path / "broken.json").unlink()

    records = read_cdx_files(sorted(tmp_path.glob("*.json")))

    assert len(records) == 3
    assert [record["timestamp"] for record in records] == [
        "20070101000000",
        "20080101000000",
        "20110101000000",
    ]
    assert records[-1]["length"] == 15
    inventory = tmp_path / "inventory.jsonl"
    write_inventory(records, inventory)
    assert len(inventory.read_text(encoding="utf-8").splitlines()) == 3
