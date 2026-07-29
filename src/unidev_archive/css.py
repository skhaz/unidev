"""Shared lexical handling for historical CSS network references."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)
CSS_IMPORT_RE = re.compile(r"@import(?:\s|/\*.*?\*/)*(['\"])(.*?)\1", re.I | re.S)
_UNSUPPORTED_NETWORK_RE = re.compile(r"(?:-webkit-)?image-set\s*\(|cross-fade\s*\(", re.I)
_ANY_NETWORK_RE = re.compile(
    r"url\s*\(|@import\b|(?:-webkit-)?image-set\s*\(|cross-fade\s*\(", re.I
)
_CSS_ESCAPE_RE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})\s?|(.))", re.S)


@dataclass(frozen=True, slots=True)
class CssReference:
    start: int
    end: int
    value: str
    is_import: bool


def _outside_matches(pattern: re.Pattern[str], value: str) -> Iterator[re.Match[str]]:
    quote: str | None = None
    escaped = False
    in_comment = False
    cursor = 0
    for match in pattern.finditer(value):
        index = cursor
        while index < match.start():
            character = value[index]
            following = value[index + 1] if index + 1 < len(value) else ""
            if in_comment:
                if character == "*" and following == "/":
                    in_comment = False
                    index += 1
            elif quote is not None:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
            elif character == "/" and following == "*":
                in_comment = True
                index += 1
            elif character in "'\"":
                quote = character
            index += 1
        outside = quote is None and not in_comment
        if outside:
            yield match
        cursor = match.end()


def css_references(value: str) -> tuple[CssReference, ...]:
    references = [
        CssReference(match.start(), match.end(), match.group(2).strip(), False)
        for match in _outside_matches(CSS_URL_RE, value)
    ]
    references.extend(
        CssReference(match.start(), match.end(), match.group(2).strip(), True)
        for match in _outside_matches(CSS_IMPORT_RE, value)
    )
    return tuple(sorted(references, key=lambda reference: reference.start))


def css_reference_values(value: str) -> tuple[str, ...]:
    return tuple(reference.value for reference in css_references(value) if reference.value)


def rewrite_css_references(
    value: str,
    replacement: Callable[[CssReference], str],
) -> str:
    references = css_references(value)
    if not references:
        return value
    output: list[str] = []
    cursor = 0
    for reference in references:
        output.append(value[cursor : reference.start])
        output.append(replacement(reference))
        cursor = reference.end
    output.append(value[cursor:])
    return "".join(output)


def _outside_text(value: str) -> str:
    output = list(value)
    quote: str | None = None
    escaped = False
    in_comment = False
    index = 0
    while index < len(value):
        character = value[index]
        following = value[index + 1] if index + 1 < len(value) else ""
        if in_comment:
            output[index] = " "
            if character == "*" and following == "/":
                output[index + 1] = " "
                in_comment = False
                index += 1
        elif quote is not None:
            output[index] = " "
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif character == "/" and following == "*":
            output[index] = output[index + 1] = " "
            in_comment = True
            index += 1
        elif character in "'\"":
            output[index] = " "
            quote = character
        index += 1
    return "".join(output)


def _decode_escape(match: re.Match[str]) -> str:
    hexadecimal, character = match.groups()
    if hexadecimal is not None:
        try:
            return chr(int(hexadecimal, 16))
        except (ValueError, OverflowError):
            return ""
    return character or ""


def has_unsupported_network_syntax(value: str) -> bool:
    outside = _outside_text(value)
    if _UNSUPPORTED_NETWORK_RE.search(outside):
        return True
    decoded = _CSS_ESCAPE_RE.sub(_decode_escape, outside)
    return decoded != outside and _ANY_NETWORK_RE.search(decoded) is not None
