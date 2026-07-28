"""Shared immutable records used by extraction and persistence."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParsedPost:
    topic_id: int | None
    forum_id: int | None
    post_id: int | None
    author_id: int | None
    author_name: str
    posted_at: str | None
    posted_at_raw: str | None
    body_html: str
    body_text: str


@dataclass(frozen=True, slots=True)
class ParsedTopicListing:
    topic_id: int
    forum_id: int | None
    title: str
    author_id: int | None
    author_name: str | None
    created_at: str | None
    last_post_id: int | None
    last_author_id: int | None
    last_author_name: str | None
    last_posted_at: str | None


@dataclass(frozen=True, slots=True)
class ParsedPage:
    era: str
    topic_id: int | None
    forum_id: int | None
    topic_title: str | None
    forum_name: str | None
    source_encoding: str
    posts: tuple[ParsedPost, ...]
    references: tuple[str, ...]
    listings: tuple[ParsedTopicListing, ...] = ()
