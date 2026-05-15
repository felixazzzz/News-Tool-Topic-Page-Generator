from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)

DEFAULT_DB_PATH = Path(__file__).parent.parent / "cache.db"


class Cache:
    """SQLite-backed cache. Covers search calls, HTTP fetches, and LLM calls."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    ttl        REAL
                )
            """)

    # ------------------------------------------------------------------
    # Low-level get / set / delete
    # ------------------------------------------------------------------

    def get(self, key: str) -> Any | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value, created_at, ttl FROM cache WHERE key = ?", (key,)
            ).fetchone()

        if row is None:
            return None

        value, created_at, ttl = row
        if ttl is not None and (time.time() - created_at) > ttl:
            self.delete(key)
            return None

        return json.loads(value)

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, created_at, ttl) "
                "VALUES (?, ?, ?, ?)",
                (key, json.dumps(value, default=str), time.time(), ttl),
            )

    def delete(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))

    # ------------------------------------------------------------------
    # Decorator
    # ------------------------------------------------------------------

    def cached(
        self,
        prefix: str,
        key_fn: Callable[..., str] | None = None,
        ttl: float | None = None,
    ) -> Callable[[F], F]:
        """Decorator for sync and async functions.

        prefix  — namespaces the key (e.g. "search", "fetch", "llm")
        key_fn  — called with the same *args/**kwargs as the wrapped function;
                  return value becomes the cache key. Defaults to hashing all args.
        ttl     — seconds before the entry expires. None = never expires.
        """

        def decorator(fn: F) -> F:
            if asyncio.iscoroutinefunction(fn):
                @functools.wraps(fn)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    k = _build_key(prefix, args, kwargs, key_fn)
                    hit = self.get(k)
                    if hit is not None:
                        logger.debug("cache hit  %s", k)
                        return hit
                    result = await fn(*args, **kwargs)
                    if result is not None:
                        self.set(k, result, ttl)
                    return result
                return async_wrapper  # type: ignore[return-value]
            else:
                @functools.wraps(fn)
                def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                    k = _build_key(prefix, args, kwargs, key_fn)
                    hit = self.get(k)
                    if hit is not None:
                        logger.debug("cache hit  %s", k)
                        return hit
                    result = fn(*args, **kwargs)
                    if result is not None:
                        self.set(k, result, ttl)
                    return result
                return sync_wrapper  # type: ignore[return-value]

        return decorator  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Key-builder helpers — import these for fine-grained key construction
# ---------------------------------------------------------------------------

def llm_cache_key(messages: list, model: str, tools: list | None = None) -> str:
    payload = {"messages": messages, "model": model, "tools": tools or []}
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def search_cache_key(query: str, **params: Any) -> str:
    raw = json.dumps({"q": query, **params}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def fetch_cache_key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Module-level default instance
# ---------------------------------------------------------------------------

_default_cache: Cache | None = None


def get_cache(db_path: Path | str = DEFAULT_DB_PATH) -> Cache:
    """Return (and lazily create) the module-level default Cache instance."""
    global _default_cache
    if _default_cache is None:
        _default_cache = Cache(db_path)
    return _default_cache


def _build_key(
    prefix: str,
    args: tuple,
    kwargs: dict,
    key_fn: Callable[..., str] | None,
) -> str:
    if key_fn is not None:
        raw = key_fn(*args, **kwargs)
    else:
        raw = json.dumps({"a": args, "k": kwargs}, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:24]
    return f"{prefix}:{digest}"
