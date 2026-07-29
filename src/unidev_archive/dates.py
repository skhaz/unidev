"""Parsing of the Portuguese and English date formats emitted by the forum."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

_MONTHS = {
    "jan": 1,
    "january": 1,
    "fev": 2,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "abr": 4,
    "apr": 4,
    "april": 4,
    "mai": 5,
    "maio": 5,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "ago": 8,
    "aug": 8,
    "august": 8,
    "set": 9,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "out": 10,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dez": 12,
    "dec": 12,
    "december": 12,
}
_NUMERIC_RE = re.compile(
    r"(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{2,4})\s*(?::|,)?\s*"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?"
)
_US_NUMERIC_RE = re.compile(
    r"(?P<month>\d{1,2})-(?P<day>\d{1,2})-(?P<year>\d{4})\s*,?\s*"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?\s*(?P<ampm>am|pm)",
    re.I,
)
_MONTH_FIRST_RE = re.compile(
    r"(?P<month>[A-Za-zÀ-ÿ]+)\s+(?P<day>\d{1,2}),\s*(?P<year>\d{4})\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?\s*(?P<ampm>am|pm)?",
    re.I,
)
_DAY_FIRST_RE = re.compile(
    r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-zÀ-ÿ]+)\s+(?P<year>\d{4})\s*,?\s*"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?\s*(?P<ampm>am|pm)?",
    re.I,
)


def _month_number(value: str) -> int | None:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    return _MONTHS.get(folded)


def parse_forum_date(value: str) -> str | None:
    """Return a timezone-free ISO timestamp because replay timezone varies."""

    normalized = " ".join(value.replace("\xa0", " ").split())
    us_numeric = _US_NUMERIC_RE.search(normalized)
    if us_numeric:
        fields = us_numeric.groupdict(default="0")
        hour = int(fields["hour"]) % 12
        if fields["ampm"].casefold() == "pm":
            hour += 12
        try:
            parsed = datetime(
                int(fields["year"]),
                int(fields["month"]),
                int(fields["day"]),
                hour,
                int(fields["minute"]),
                int(fields["second"]),
            )
        except ValueError:
            return None
        return parsed.isoformat(timespec="seconds")

    numeric = _NUMERIC_RE.search(normalized)
    if numeric:
        fields = numeric.groupdict(default="0")
        year = int(fields["year"])
        year += 2000 if year < 70 else 1900 if year < 100 else 0
        try:
            parsed = datetime(
                year,
                int(fields["month"]),
                int(fields["day"]),
                int(fields["hour"]),
                int(fields["minute"]),
                int(fields["second"]),
            )
        except ValueError:
            return None
        return parsed.isoformat(timespec="seconds")

    match = _MONTH_FIRST_RE.search(normalized) or _DAY_FIRST_RE.search(normalized)
    if not match:
        return None
    fields = match.groupdict(default="")
    month = _month_number(fields["month"])
    if month is None:
        return None
    hour = int(fields["hour"])
    ampm = fields.get("ampm", "").casefold()
    if ampm:
        hour %= 12
        if ampm == "pm":
            hour += 12
    try:
        parsed = datetime(
            int(fields["year"]),
            month,
            int(fields["day"]),
            hour,
            int(fields["minute"]),
            int(fields["second"] or "0"),
        )
    except ValueError:
        return None
    return parsed.isoformat(timespec="seconds")
