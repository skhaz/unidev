"""Small shared parser for the URL-bearing subset of HTML srcset."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SrcsetCandidate:
    url: str
    descriptor: str


def parse_srcset(value: str) -> tuple[SrcsetCandidate, ...]:
    candidates: list[SrcsetCandidate] = []
    length = len(value)
    index = 0
    while index < length:
        while index < length and (value[index].isspace() or value[index] == ","):
            index += 1
        if index >= length:
            break
        url_start = index
        while index < length and not value[index].isspace():
            index += 1
        url = value[url_start:index]
        trailing_separator = not url.startswith("data:") and url.endswith(",")
        if trailing_separator:
            url = url.rstrip(",")
        while index < length and value[index].isspace():
            index += 1
        descriptor_start = index
        if not trailing_separator:
            while index < length and value[index] != ",":
                index += 1
        descriptor = value[descriptor_start:index].strip()
        if url:
            candidates.append(SrcsetCandidate(url=url, descriptor=descriptor))
        if index < length and value[index] == ",":
            index += 1
    return tuple(candidates)


def serialize_srcset(candidates: tuple[SrcsetCandidate, ...]) -> str:
    return ", ".join(
        candidate.url + (f" {candidate.descriptor}" if candidate.descriptor else "")
        for candidate in candidates
    )
