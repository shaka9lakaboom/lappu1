"""Exact result cache of the ModelGateway (ADR 0004).

A request is identified by provider + model + task_type + prompt_version + input
hash (the hash already covers the system prompt, messages and response schema).
Only VALIDATED outputs are ever stored: failures, rate limits and invalid outputs
are never cached, so they can never be served as a success. Outputs are
re-validated when served, so a cached value that no longer satisfies the current
validator is ignored rather than trusted.

* `InMemoryResultCache` - bounded LRU, per process (structured outputs and vectors).
* `DbResultCache` - durable tier for structured outputs, read from `model_runs`
  itself (the recorder already stores each validated output with its cache key).
* `TieredResultCache` - memory first, then the durable tier.

Embedding vectors are cached in memory only (skill documents also have their own
durable content-hash cache in `skill_embeddings`).
"""

import hashlib
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from psycopg_pool import ConnectionPool


@dataclass(frozen=True)
class CacheKey:
    provider: str
    model: str
    task_type: str
    prompt_version: str
    input_hash: str

    @property
    def digest(self) -> str:
        """Stored in model_runs.cache_key."""
        parts = [self.provider, self.model, self.task_type, self.prompt_version, self.input_hash]
        return hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CachedResult:
    """A validated output and the model_runs row that produced it with a provider request."""

    source_run_id: UUID
    output_hash: str | None
    output: dict[str, Any] | None = None  # structured generation
    vectors: tuple[tuple[float, ...], ...] | None = None  # embeddings (normalized)


class ResultCache(Protocol):
    def get(self, key: CacheKey) -> CachedResult | None: ...

    def put(self, key: CacheKey, result: CachedResult) -> None: ...


class InMemoryResultCache:
    def __init__(self, max_entries: int = 512) -> None:
        self.max_entries = max_entries
        self._entries: OrderedDict[str, CachedResult] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: CacheKey) -> CachedResult | None:
        with self._lock:
            found = self._entries.get(key.digest)
            if found is not None:
                self._entries.move_to_end(key.digest)
            return found

    def put(self, key: CacheKey, result: CachedResult) -> None:
        if self.max_entries <= 0:
            return
        with self._lock:
            self._entries[key.digest] = result
            self._entries.move_to_end(key.digest)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)


class DbResultCache:
    """Durable tier: the newest provider-produced, validated output for the key."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def get(self, key: CacheKey) -> CachedResult | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                select id, output_hash, output from public.model_runs
                 where cache_key = %s and status = 'SUCCEEDED'
                   and cache_source_run_id is null and output is not null
                 order by created_at desc
                 limit 1
                """,
                (key.digest,),
            ).fetchone()
        if row is None:
            return None
        return CachedResult(source_run_id=row[0], output_hash=row[1], output=row[2])

    def put(self, key: CacheKey, result: CachedResult) -> None:
        # Nothing to do: the recorder has already written the source run with its key.
        return None


class TieredResultCache:
    def __init__(self, memory: InMemoryResultCache, durable: ResultCache) -> None:
        self.memory = memory
        self.durable = durable

    def get(self, key: CacheKey) -> CachedResult | None:
        found = self.memory.get(key)
        if found is not None:
            return found
        found = self.durable.get(key)
        if found is not None:
            self.memory.put(key, found)
        return found

    def put(self, key: CacheKey, result: CachedResult) -> None:
        self.memory.put(key, result)
        self.durable.put(key, result)
