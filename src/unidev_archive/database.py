# pyright: reportMissingImports=false
"""Build-time SQLite persistence for the static restored forum."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from unidev_archive.models import ParsedPage
from unidev_archive.urls import canonical_url, era_for_url, resource_kind

_SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS captures (
    capture_id INTEGER PRIMARY KEY,
    canonical_url TEXT NOT NULL,
    original_url TEXT NOT NULL,
    timestamp TEXT NOT NULL CHECK(length(timestamp) = 14),
    era TEXT,
    kind TEXT NOT NULL,
    mimetype TEXT,
    statuscode INTEGER,
    cdx_digest TEXT,
    cdx_length INTEGER,
    raw_sha256 TEXT,
    source_encoding TEXT,
    fetch_status TEXT NOT NULL DEFAULT 'pending',
    http_status INTEGER,
    response_headers_json TEXT,
    fetched_at TEXT,
    error TEXT,
    UNIQUE(original_url, timestamp)
);
CREATE INDEX IF NOT EXISTS captures_url_time ON captures(canonical_url, timestamp);
CREATE INDEX IF NOT EXISTS captures_queue ON captures(fetch_status, kind, timestamp);
CREATE INDEX IF NOT EXISTS captures_blob ON captures(raw_sha256) WHERE raw_sha256 IS NOT NULL;

CREATE TABLE IF NOT EXISTS blobs (
    sha256 TEXT PRIMARY KEY CHECK(length(sha256) = 64),
    relative_path TEXT NOT NULL UNIQUE,
    byte_length INTEGER NOT NULL,
    mimetype TEXT
);

CREATE TABLE IF NOT EXISTS resource_references (
    referrer_capture_id INTEGER NOT NULL REFERENCES captures(capture_id) ON DELETE CASCADE,
    target_url TEXT NOT NULL,
    kind TEXT NOT NULL,
    nearest_capture_id INTEGER REFERENCES captures(capture_id),
    PRIMARY KEY(referrer_capture_id, target_url)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS references_target ON resource_references(target_url, kind);

CREATE TABLE IF NOT EXISTS forums (
    forum_id INTEGER PRIMARY KEY,
    name TEXT,
    first_seen TEXT,
    last_seen TEXT
);

CREATE TABLE IF NOT EXISTS topics (
    topic_id INTEGER PRIMARY KEY,
    forum_id INTEGER REFERENCES forums(forum_id),
    title TEXT,
    first_posted_at TEXT,
    last_posted_at TEXT,
    first_seen TEXT,
    last_seen TEXT
);
CREATE INDEX IF NOT EXISTS topics_forum ON topics(forum_id, last_posted_at);

CREATE TABLE IF NOT EXISTS users (
    user_pk INTEGER PRIMARY KEY,
    historical_id INTEGER UNIQUE,
    username TEXT NOT NULL,
    username_norm TEXT NOT NULL UNIQUE,
    first_posted_at TEXT,
    last_posted_at TEXT,
    post_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS users_activity ON users(last_posted_at DESC);

CREATE TABLE IF NOT EXISTS posts (
    post_pk INTEGER PRIMARY KEY,
    historical_id INTEGER UNIQUE,
    identity_sha256 TEXT NOT NULL UNIQUE CHECK(length(identity_sha256) = 64),
    topic_id INTEGER REFERENCES topics(topic_id),
    forum_id INTEGER REFERENCES forums(forum_id),
    user_pk INTEGER REFERENCES users(user_pk),
    author_name TEXT NOT NULL,
    author_norm TEXT NOT NULL,
    topic_title TEXT,
    forum_name TEXT,
    posted_at TEXT,
    posted_at_raw TEXT,
    body_html TEXT NOT NULL,
    body_text TEXT NOT NULL,
    first_capture_id INTEGER NOT NULL REFERENCES captures(capture_id),
    best_capture_id INTEGER NOT NULL REFERENCES captures(capture_id),
    best_capture_timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS posts_topic_date ON posts(topic_id, posted_at, post_pk);
CREATE INDEX IF NOT EXISTS posts_author_date ON posts(author_norm, posted_at DESC, post_pk);
CREATE INDEX IF NOT EXISTS posts_date ON posts(posted_at DESC, post_pk);

CREATE TABLE IF NOT EXISTS post_sources (
    post_pk INTEGER NOT NULL REFERENCES posts(post_pk) ON DELETE CASCADE,
    capture_id INTEGER NOT NULL REFERENCES captures(capture_id) ON DELETE CASCADE,
    PRIMARY KEY(post_pk, capture_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS activity_evidence (
    identity TEXT PRIMARY KEY,
    user_pk INTEGER NOT NULL REFERENCES users(user_pk),
    topic_id INTEGER NOT NULL REFERENCES topics(topic_id),
    forum_id INTEGER REFERENCES forums(forum_id),
    role TEXT NOT NULL CHECK(role IN ('topic_author', 'last_poster')),
    post_id INTEGER,
    posted_at TEXT,
    topic_title TEXT NOT NULL,
    forum_name TEXT,
    best_capture_id INTEGER NOT NULL REFERENCES captures(capture_id),
    best_capture_timestamp TEXT NOT NULL
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS activity_user_date
    ON activity_evidence(user_pk, posted_at DESC, topic_id);

CREATE TABLE IF NOT EXISTS activity_sources (
    identity TEXT NOT NULL REFERENCES activity_evidence(identity) ON DELETE CASCADE,
    capture_id INTEGER NOT NULL REFERENCES captures(capture_id) ON DELETE CASCADE,
    PRIMARY KEY(identity, capture_id)
) WITHOUT ROWID;
"""


@dataclass(frozen=True, slots=True)
class CaptureRecord:
    timestamp: str
    original_url: str
    statuscode: int | None = None
    mimetype: str | None = None
    digest: str | None = None
    length: int | None = None


def normalize_username(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        character for character in folded if not unicodedata.combining(character)
    ).strip()


def post_identity(
    topic_id: int | None,
    author_name: str,
    posted_at: str | None,
    posted_at_raw: str | None,
    body_text: str,
) -> str:
    identity = "\x1f".join(
        (
            str(topic_id or ""),
            normalize_username(author_name),
            posted_at or posted_at_raw or "",
            body_text,
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class ArchiveDB:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    def __enter__(self) -> ArchiveDB:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        self.connection.executescript(_SCHEMA)

    def add_captures(self, records: Iterable[CaptureRecord]) -> int:
        rows = [
            (
                canonical_url(record.original_url),
                record.original_url,
                record.timestamp,
                era_for_url(record.original_url, record.timestamp),
                resource_kind(record.original_url),
                record.mimetype,
                record.statuscode,
                record.digest,
                record.length,
            )
            for record in records
        ]
        before = self.connection.total_changes
        with self.connection:
            self.connection.executemany(
                """
                INSERT INTO captures(
                    canonical_url, original_url, timestamp, era, kind,
                    mimetype, statuscode, cdx_digest, cdx_length
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(original_url, timestamp) DO UPDATE SET
                    mimetype=coalesce(excluded.mimetype, captures.mimetype),
                    statuscode=coalesce(excluded.statuscode, captures.statuscode),
                    cdx_digest=coalesce(excluded.cdx_digest, captures.cdx_digest),
                    cdx_length=coalesce(excluded.cdx_length, captures.cdx_length)
                """,
                rows,
            )
        return self.connection.total_changes - before

    def record_blob(
        self,
        capture_id: int,
        sha256: str,
        relative_path: str,
        byte_length: int,
        mimetype: str | None,
        source_encoding: str | None = None,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO blobs(sha256, relative_path, byte_length, mimetype)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(sha256) DO UPDATE SET
                    mimetype=coalesce(blobs.mimetype, excluded.mimetype)
                """,
                (sha256, relative_path, byte_length, mimetype),
            )
            self.connection.execute(
                """
                UPDATE captures SET raw_sha256=?, source_encoding=?, fetch_status='fetched', error=NULL
                WHERE capture_id=?
                """,
                (sha256, source_encoding, capture_id),
            )

    def add_references(self, capture_id: int, references: Iterable[str]) -> None:
        with self.connection:
            self.connection.executemany(
                """
                INSERT INTO resource_references(referrer_capture_id, target_url, kind)
                VALUES (?, ?, ?)
                ON CONFLICT(referrer_capture_id, target_url) DO UPDATE SET kind=excluded.kind
                """,
                ((capture_id, url, resource_kind(url)) for url in references),
            )

    def capture_id(self, original_url: str, timestamp: str) -> int:
        row = self.connection.execute(
            "SELECT capture_id FROM captures WHERE original_url=? AND timestamp=?",
            (original_url, timestamp),
        ).fetchone()
        if row is None:
            raise KeyError((original_url, timestamp))
        return int(row["capture_id"])

    def ingest_page(self, capture_id: int, page: ParsedPage) -> int:
        capture = self.connection.execute(
            "SELECT timestamp FROM captures WHERE capture_id=?", (capture_id,)
        ).fetchone()
        if capture is None:
            raise KeyError(capture_id)
        timestamp = str(capture["timestamp"])
        if not page.posts:
            return 0

        forum_ids = {post.forum_id for post in page.posts if post.forum_id is not None}
        topic_ids = {post.topic_id for post in page.posts if post.topic_id is not None}
        user_rows = {
            (post.author_id, post.author_name, normalize_username(post.author_name))
            for post in page.posts
        }
        with self.connection:
            self.connection.executemany(
                """
                INSERT INTO forums(forum_id, name, first_seen, last_seen) VALUES (?, ?, ?, ?)
                ON CONFLICT(forum_id) DO UPDATE SET
                    name=coalesce(excluded.name, forums.name),
                    first_seen=min(forums.first_seen, excluded.first_seen),
                    last_seen=max(forums.last_seen, excluded.last_seen)
                """,
                ((forum_id, page.forum_name, timestamp, timestamp) for forum_id in forum_ids),
            )
            self.connection.executemany(
                """
                INSERT INTO topics(topic_id, forum_id, title, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(topic_id) DO UPDATE SET
                    forum_id=coalesce(excluded.forum_id, topics.forum_id),
                    title=coalesce(excluded.title, topics.title),
                    first_seen=min(topics.first_seen, excluded.first_seen),
                    last_seen=max(topics.last_seen, excluded.last_seen)
                """,
                (
                    (topic_id, page.forum_id, page.topic_title, timestamp, timestamp)
                    for topic_id in topic_ids
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO users(historical_id, username, username_norm) VALUES (?, ?, ?)
                ON CONFLICT(username_norm) DO UPDATE SET
                    historical_id=coalesce(users.historical_id, excluded.historical_id),
                    username=CASE
                        WHEN excluded.historical_id IS NOT NULL THEN excluded.username
                        ELSE users.username
                    END
                """,
                user_rows,
            )

            norms = tuple(row[2] for row in user_rows)
            users = {
                str(row["username_norm"]): int(row["user_pk"])
                for row in self.connection.execute(
                    """
                    SELECT user_pk, username_norm FROM users
                    WHERE username_norm IN (SELECT value FROM json_each(?))
                    """,
                    (json.dumps(norms),),
                )
            }
            post_rows = []
            identities: list[str] = []
            historical_ids: list[int] = []
            for post in page.posts:
                identity = post_identity(
                    post.topic_id,
                    post.author_name,
                    post.posted_at,
                    post.posted_at_raw,
                    post.body_text,
                )
                identities.append(identity)
                if post.post_id is not None:
                    historical_ids.append(post.post_id)
                post_rows.append(
                    (
                        post.post_id,
                        identity,
                        post.topic_id,
                        post.forum_id,
                        users[normalize_username(post.author_name)],
                        post.author_name,
                        normalize_username(post.author_name),
                        page.topic_title,
                        page.forum_name,
                        post.posted_at,
                        post.posted_at_raw,
                        post.body_html,
                        post.body_text,
                        capture_id,
                        capture_id,
                        timestamp,
                    )
                )
            self.connection.executemany(
                """
                INSERT INTO posts(
                    historical_id, identity_sha256, topic_id, forum_id, user_pk,
                    author_name, author_norm, topic_title, forum_name, posted_at,
                    posted_at_raw, body_html, body_text, first_capture_id,
                    best_capture_id, best_capture_timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(identity_sha256) DO UPDATE SET
                    historical_id=coalesce(posts.historical_id, excluded.historical_id),
                    topic_title=coalesce(excluded.topic_title, posts.topic_title),
                    forum_name=coalesce(excluded.forum_name, posts.forum_name),
                    best_capture_id=CASE WHEN excluded.best_capture_timestamp > posts.best_capture_timestamp
                        THEN excluded.best_capture_id ELSE posts.best_capture_id END,
                    best_capture_timestamp=max(posts.best_capture_timestamp, excluded.best_capture_timestamp)
                ON CONFLICT(historical_id) DO UPDATE SET
                    identity_sha256=excluded.identity_sha256,
                    user_pk=excluded.user_pk,
                    author_name=excluded.author_name,
                    author_norm=excluded.author_norm,
                    topic_title=coalesce(excluded.topic_title, posts.topic_title),
                    forum_name=coalesce(excluded.forum_name, posts.forum_name),
                    posted_at=coalesce(excluded.posted_at, posts.posted_at),
                    posted_at_raw=coalesce(excluded.posted_at_raw, posts.posted_at_raw),
                    body_html=CASE WHEN excluded.best_capture_timestamp >= posts.best_capture_timestamp
                        THEN excluded.body_html ELSE posts.body_html END,
                    body_text=CASE WHEN excluded.best_capture_timestamp >= posts.best_capture_timestamp
                        THEN excluded.body_text ELSE posts.body_text END,
                    best_capture_id=CASE WHEN excluded.best_capture_timestamp >= posts.best_capture_timestamp
                        THEN excluded.best_capture_id ELSE posts.best_capture_id END,
                    best_capture_timestamp=max(posts.best_capture_timestamp, excluded.best_capture_timestamp)
                """,
                post_rows,
            )

            matched = self.connection.execute(
                """
                SELECT post_pk FROM posts
                WHERE identity_sha256 IN (SELECT value FROM json_each(?))
                   OR historical_id IN (SELECT value FROM json_each(?))
                """,
                (json.dumps(identities), json.dumps(historical_ids)),
            ).fetchall()
            self.connection.executemany(
                "INSERT OR IGNORE INTO post_sources(post_pk, capture_id) VALUES (?, ?)",
                ((int(row["post_pk"]), capture_id) for row in matched),
            )
            self._refresh_stats(
                topic_ids, {normalize_username(post.author_name) for post in page.posts}
            )
        return len(page.posts)

    def ingest_listings(self, capture_id: int, page: ParsedPage) -> int:
        capture = self.connection.execute(
            "SELECT timestamp FROM captures WHERE capture_id=?", (capture_id,)
        ).fetchone()
        if capture is None:
            raise KeyError(capture_id)
        timestamp = str(capture["timestamp"])
        listings = page.listings
        if not listings:
            return 0

        users_to_add: set[tuple[int | None, str, str]] = set()
        for listing in listings:
            for user_id, username in (
                (listing.author_id, listing.author_name),
                (listing.last_author_id, listing.last_author_name),
            ):
                if username:
                    users_to_add.add((user_id, username, normalize_username(username)))
        with self.connection:
            if page.forum_id is not None:
                self.connection.execute(
                    """
                    INSERT INTO forums(forum_id, name, first_seen, last_seen) VALUES (?, ?, ?, ?)
                    ON CONFLICT(forum_id) DO UPDATE SET
                        name=coalesce(excluded.name, forums.name),
                        first_seen=min(forums.first_seen, excluded.first_seen),
                        last_seen=max(forums.last_seen, excluded.last_seen)
                    """,
                    (page.forum_id, page.forum_name, timestamp, timestamp),
                )
            self.connection.executemany(
                """
                INSERT INTO topics(
                    topic_id, forum_id, title, first_posted_at, last_posted_at,
                    first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(topic_id) DO UPDATE SET
                    forum_id=coalesce(excluded.forum_id, topics.forum_id),
                    title=coalesce(excluded.title, topics.title),
                    first_posted_at=coalesce(topics.first_posted_at, excluded.first_posted_at),
                    last_posted_at=coalesce(excluded.last_posted_at, topics.last_posted_at),
                    first_seen=min(topics.first_seen, excluded.first_seen),
                    last_seen=max(topics.last_seen, excluded.last_seen)
                """,
                (
                    (
                        listing.topic_id,
                        listing.forum_id,
                        listing.title,
                        listing.created_at,
                        listing.last_posted_at,
                        timestamp,
                        timestamp,
                    )
                    for listing in listings
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO users(historical_id, username, username_norm) VALUES (?, ?, ?)
                ON CONFLICT(username_norm) DO UPDATE SET
                    historical_id=coalesce(users.historical_id, excluded.historical_id),
                    username=CASE WHEN excluded.historical_id IS NOT NULL
                        THEN excluded.username ELSE users.username END
                """,
                users_to_add,
            )
            norms = tuple(row[2] for row in users_to_add)
            user_map = {
                str(row["username_norm"]): int(row["user_pk"])
                for row in self.connection.execute(
                    """
                    SELECT user_pk, username_norm FROM users
                    WHERE username_norm IN (SELECT value FROM json_each(?))
                    """,
                    (json.dumps(norms),),
                )
            }
            evidence_rows: list[tuple[object, ...]] = []
            for listing in listings:
                participants = (
                    (
                        "topic_author",
                        listing.author_name,
                        listing.created_at,
                        None,
                    ),
                    (
                        "last_poster",
                        listing.last_author_name,
                        listing.last_posted_at,
                        listing.last_post_id,
                    ),
                )
                for role, username, posted_at, post_id in participants:
                    if not username:
                        continue
                    username_norm = normalize_username(username)
                    day = posted_at[:10] if posted_at else "unknown"
                    identity = (
                        f"author:{listing.topic_id}:{username_norm}"
                        if role == "topic_author"
                        else f"last:{listing.topic_id}:{username_norm}:{day}"
                    )
                    evidence_rows.append(
                        (
                            identity,
                            user_map[username_norm],
                            listing.topic_id,
                            listing.forum_id,
                            role,
                            post_id,
                            posted_at,
                            listing.title,
                            page.forum_name,
                            capture_id,
                            timestamp,
                        )
                    )
            self.connection.executemany(
                """
                INSERT INTO activity_evidence(
                    identity, user_pk, topic_id, forum_id, role, post_id, posted_at,
                    topic_title, forum_name, best_capture_id, best_capture_timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(identity) DO UPDATE SET
                    post_id=coalesce(excluded.post_id, activity_evidence.post_id),
                    posted_at=CASE
                        WHEN excluded.post_id IS NOT NULL AND activity_evidence.post_id IS NULL
                        THEN excluded.posted_at ELSE activity_evidence.posted_at END,
                    forum_name=coalesce(excluded.forum_name, activity_evidence.forum_name),
                    best_capture_id=CASE
                        WHEN excluded.post_id IS NOT NULL AND activity_evidence.post_id IS NULL
                        THEN excluded.best_capture_id ELSE activity_evidence.best_capture_id END,
                    best_capture_timestamp=max(
                        activity_evidence.best_capture_timestamp,
                        excluded.best_capture_timestamp
                    )
                """,
                evidence_rows,
            )
            identities = tuple(str(row[0]) for row in evidence_rows)
            self.connection.executemany(
                """
                INSERT OR IGNORE INTO activity_sources(identity, capture_id)
                VALUES (?, ?)
                """,
                ((identity, capture_id) for identity in identities),
            )
            self._refresh_stats(
                {listing.topic_id for listing in listings},
                {row[2] for row in users_to_add},
            )
        return len(listings)

    def _refresh_stats(self, topic_ids: set[int], usernames: set[str]) -> None:
        if topic_ids:
            self.connection.execute(
                """
                UPDATE topics SET
                    first_posted_at=(
                        SELECT min(posted_at) FROM (
                            SELECT posted_at FROM posts WHERE posts.topic_id=topics.topic_id
                            UNION ALL
                            SELECT posted_at FROM activity_evidence
                            WHERE activity_evidence.topic_id=topics.topic_id
                        ) WHERE posted_at IS NOT NULL
                    ),
                    last_posted_at=(
                        SELECT max(posted_at) FROM (
                            SELECT posted_at FROM posts WHERE posts.topic_id=topics.topic_id
                            UNION ALL
                            SELECT posted_at FROM activity_evidence
                            WHERE activity_evidence.topic_id=topics.topic_id
                        ) WHERE posted_at IS NOT NULL
                    )
                WHERE topic_id IN (SELECT value FROM json_each(?))
                """,
                (json.dumps(tuple(topic_ids)),),
            )
        if usernames:
            self.connection.execute(
                """
                UPDATE users SET
                    first_posted_at=(
                        SELECT min(posted_at) FROM (
                            SELECT posted_at FROM posts WHERE posts.user_pk=users.user_pk
                            UNION ALL
                            SELECT posted_at FROM activity_evidence
                            WHERE activity_evidence.user_pk=users.user_pk
                        ) WHERE posted_at IS NOT NULL
                    ),
                    last_posted_at=(
                        SELECT max(posted_at) FROM (
                            SELECT posted_at FROM posts WHERE posts.user_pk=users.user_pk
                            UNION ALL
                            SELECT posted_at FROM activity_evidence
                            WHERE activity_evidence.user_pk=users.user_pk
                        ) WHERE posted_at IS NOT NULL
                    ),
                    post_count=(SELECT count(*) FROM posts WHERE posts.user_pk=users.user_pk)
                WHERE username_norm IN (SELECT value FROM json_each(?))
                """,
                (json.dumps(tuple(usernames)),),
            )

    def counts(self) -> dict[str, int]:
        row = self.connection.execute(
            """
            SELECT
                (SELECT count(*) FROM captures) AS captures,
                (SELECT count(*) FROM blobs) AS blobs,
                (SELECT count(*) FROM topics) AS topics,
                (SELECT count(*) FROM posts) AS posts,
                (SELECT count(*) FROM users) AS users,
                (SELECT count(*) FROM activity_evidence) AS activities
            """
        ).fetchone()
        return {key: int(value) for key, value in dict(row).items()} if row else {}

    def pending_captures(self, kinds: Sequence[str], limit: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT * FROM captures
            WHERE fetch_status='pending' AND statuscode=200
              AND kind IN (SELECT value FROM json_each(?))
            ORDER BY CASE kind WHEN 'page' THEN 0 WHEN 'asset' THEN 1 ELSE 2 END,
                     timestamp, capture_id
            LIMIT ?
            """,
            (json.dumps(tuple(kinds)), limit),
        ).fetchall()
