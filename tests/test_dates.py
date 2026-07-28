# pyright: reportMissingImports=false
from __future__ import annotations

import pytest

from unidev_archive.dates import parse_forum_date


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("07/09/2004 18:57:37", "2004-09-07T18:57:37"),
        ("Sex Jul 15, 2005 1:28 pm", "2005-07-15T13:28:00"),
        ("Tue May 05, 2009 5:22 pm", "2009-05-05T17:22:00"),
        ("12 Set 2009, 22:25", "2009-09-12T22:25:00"),
        ("01 Dez 2003, 13:16", "2003-12-01T13:16:00"),
    ],
)
def test_parses_historical_forum_dates(raw: str, expected: str) -> None:
    assert parse_forum_date(raw) == expected


def test_rejects_invalid_date() -> None:
    assert parse_forum_date("31 Fev 2009, 22:25") is None
