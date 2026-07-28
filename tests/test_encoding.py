# pyright: reportMissingImports=false
from __future__ import annotations

import unicodedata

from unidev_archive.encoding import decode_html


def test_decodes_declared_windows_1252_as_utf8_text() -> None:
    raw = (
        b'<meta http-equiv="Content-Type" content="text/html; charset=ISO-8859-1">'
        + "Fórum — programação".encode("cp1252")
    )

    decoded = decode_html(raw)

    assert decoded.text.endswith("Fórum — programação")
    assert decoded.source_encoding == "windows-1252"
    assert decoded.text == unicodedata.normalize("NFC", decoded.text)


def test_repairs_mixed_utf8_and_single_byte_windows_1252() -> None:
    raw = b'<meta charset="UTF-8"><p>Programa\xc3\xa7\xc3\xa3o \xa9 UniDev</p>'

    decoded = decode_html(raw)

    assert "Programação © UniDev" in decoded.text
    assert decoded.source_encoding == "utf-8/windows-1252-mixed"
    assert "�" not in decoded.text
    assert "ProgramaÃ" not in decoded.text


def test_normalizes_unicode_to_nfc() -> None:
    raw = '<meta charset="utf-8">Cafe\u0301'.encode()

    assert decode_html(raw).text.endswith("Café")
