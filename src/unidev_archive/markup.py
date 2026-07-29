"""Shared XML/HTML qualified-name normalization."""

from __future__ import annotations

REMOVED_ELEMENT_NAMES = frozenset(
    {
        "animate",
        "animatemotion",
        "animatetransform",
        "applet",
        "base",
        "discard",
        "embed",
        "foreignobject",
        "frame",
        "frameset",
        "iframe",
        "object",
        "script",
        "set",
    }
)


def local_name(value: object) -> str:
    return str(value).rsplit("}", 1)[-1].rsplit(":", 1)[-1].casefold()
