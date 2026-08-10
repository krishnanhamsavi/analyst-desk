"""A tiny SQLite-backed cache for tool responses.

Why this exists:
  * Demos must be fast and repeatable -- a second run of the same ticker is instant.
  * Free APIs rate-limit. Caching keeps us well under the limits.
  * DEMO_MODE=true serves cache only, so the whole product works offline.

Keys are (tool, key) where `key` is whatever identifies the request, e.g.
"AAPL|1y". Entries carry a fetched_at timestamp and expire after CACHE_TTL_HOURS.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from core.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_cache (
    tool        TEXT NOT NULL,
    key         TEXT NOT NULL,
    payload     TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (tool, key)
);
"""

_lock = threading.Lock()
_initialised = False


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    global _initialised
    conn = sqlite3.connect(settings.cache_db_path, timeout=10)
    try:
        if not _initialised:
            with _lock:
                conn.executescript(_SCHEMA)
                conn.commit()
                _initialised = True
        yield conn
    finally:
        conn.close()


class CacheEntry:
    __slots__ = ("payload", "fetched_at")

    def __init__(self, payload: Any, fetched_at: datetime) -> None:
        self.payload = payload
        self.fetched_at = fetched_at

    @property
    def age(self) -> timedelta:
        return datetime.now(timezone.utc) - self.fetched_at


def get(tool: str, key: str, ttl_hours: int | None = None) -> CacheEntry | None:
    """Return a fresh cache entry, or None if missing/stale.

    In DEMO_MODE stale entries are still returned -- offline beats fresh.
    """
    ttl = settings.cache_ttl_hours if ttl_hours is None else ttl_hours
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT payload, fetched_at FROM tool_cache WHERE tool = ? AND key = ?",
                (tool, key),
            ).fetchone()
    except sqlite3.Error:
        return None

    if row is None:
        return None

    try:
        payload = json.loads(row[0])
        fetched_at = datetime.fromisoformat(row[1])
    except (json.JSONDecodeError, ValueError):
        return None

    entry = CacheEntry(payload, fetched_at)
    if settings.demo_mode:
        return entry
    if entry.age > timedelta(hours=ttl):
        return None
    return entry


def put(tool: str, key: str, payload: Any) -> None:
    """Store a payload. Cache failures are never fatal -- worst case we refetch."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO tool_cache (tool, key, payload, fetched_at) "
                "VALUES (?, ?, ?, ?)",
                (tool, key, json.dumps(payload, default=str), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
    except (sqlite3.Error, TypeError):
        pass


def clear(tool: str | None = None) -> int:
    """Wipe the cache (or just one tool's entries). Returns rows deleted."""
    try:
        with _connect() as conn:
            if tool:
                cur = conn.execute("DELETE FROM tool_cache WHERE tool = ?", (tool,))
            else:
                cur = conn.execute("DELETE FROM tool_cache")
            conn.commit()
            return cur.rowcount
    except sqlite3.Error:
        return 0


def cached(tool: str, key: str, fetch, ttl_hours: int | None = None) -> tuple[Any, bool]:
    """Read-through cache helper.

    Returns (payload, from_cache). If DEMO_MODE is on and nothing is cached,
    raises LookupError -- callers turn that into a clean 'no data' ToolResult.
    """
    entry = get(tool, key, ttl_hours)
    if entry is not None:
        return entry.payload, True

    if settings.demo_mode:
        raise LookupError(f"DEMO_MODE is on and no cached data exists for {tool}:{key}")

    payload = fetch()
    put(tool, key, payload)
    return payload, False
