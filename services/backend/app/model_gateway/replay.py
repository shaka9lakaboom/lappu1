"""Record / replay of provider responses (P8; ADR 0008). For the benchmark and end-to-end runs
ONLY - never a production provider.

    RecordingProvider(real)   passes every request to the real provider and keeps its response
    ReplayProvider(file)      answers from a recording; a request it has not seen raises
                              StaleRecordingError (a changed prompt, schema, model or input needs
                              a new live recording - it is never answered with something else)

A request's identity is a sha256 over everything the provider sees: the operation, model,
system prompt, messages and JSON schema (generation), or the model, texts, titles, input type and
dimension (embedding). Responses are stored as the provider returned them (the text of a
structured answer; the vectors of an embedding), so a replay drives the gateway, its validation,
repair and cache exactly as the live run did.

Replay refuses to run against a non-local database (`assert_local_database`): recorded, possibly
scripted answers must never reach hosted data.
"""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.model_gateway.types import (
    Message,
    ProviderEmbeddingResponse,
    ProviderError,
    ProviderTextResponse,
    ProviderUsage,
)

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "db", "postgres"})


class StaleRecordingError(ProviderError):
    """The replayed run asked for something the recording does not contain. The gateway records
    it as a FAILED run (code STALE_RECORDING); the replay provider also lists every miss."""

    def __init__(self, key: str, what: str) -> None:
        super().__init__(
            "failed", "STALE_RECORDING", f"no recording for {what} (key {key[:12]}): re-record live"
        )
        self.key = key


def assert_local_database(database_url: str) -> None:
    host = urlsplit(database_url).hostname or ""
    if host not in LOCAL_HOSTS:
        raise RuntimeError(
            f"record / replay runs only against a local database (got host {host!r})"
        )


def _digest(payload: list[Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def generation_key(
    model: str, system: str | None, messages: Sequence[Message], json_schema: dict[str, Any]
) -> str:
    return _digest(
        [
            "generate",
            model,
            system or "",
            [[m.role, m.content] for m in messages],
            json_schema,
        ]
    )


def embedding_key(
    model: str,
    texts: Sequence[str],
    titles: Sequence[str | None] | None,
    input_type: str,
    output_dimension: int,
) -> str:
    return _digest(["embed", model, list(texts), list(titles or []), input_type, output_dimension])


class RecordingProvider:
    """Wraps the real provider; `entries` accumulates one record per distinct request."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider
        self.name = provider.name
        self.entries: dict[str, dict[str, Any]] = {}
        self.requests = 0

    def generate_json(
        self,
        *,
        model: str,
        system: str | None,
        messages: Sequence[Message],
        json_schema: dict[str, Any],
        timeout: float,
    ) -> ProviderTextResponse:
        self.requests += 1
        response = self._provider.generate_json(
            model=model, system=system, messages=messages, json_schema=json_schema, timeout=timeout
        )
        key = generation_key(model, system, messages, json_schema)
        self.entries[key] = {
            "key": key,
            "op": "generate",
            "model": model,
            "system_head": (system or "")[:60],
            "text": response.text,
            "usage": response.usage.__dict__ if response.usage else None,
        }
        return response

    def embed(
        self,
        *,
        model: str,
        texts: Sequence[str],
        titles: Sequence[str | None] | None,
        input_type: str,
        output_dimension: int,
        timeout: float,
    ) -> ProviderEmbeddingResponse:
        self.requests += 1
        response = self._provider.embed(
            model=model,
            texts=texts,
            titles=titles,
            input_type=input_type,
            output_dimension=output_dimension,
            timeout=timeout,
        )
        key = embedding_key(model, texts, titles, input_type, output_dimension)
        self.entries[key] = {
            "key": key,
            "op": "embed",
            "model": model,
            "count": len(texts),
            "vectors": response.vectors,
        }
        return response

    def write(self, path: Path, *, merge: bool = True) -> int:
        """Write the recording (JSON Lines, sorted by key). With `merge`, earlier entries of the
        file that were not re-recorded are kept."""
        entries = dict(load_recording(path)) if merge and path.exists() else {}
        entries.update(self.entries)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as out:
            for key in sorted(entries):
                out.write(json.dumps(entries[key], ensure_ascii=False, sort_keys=True) + "\n")
        return len(entries)


def load_recording(path: Path) -> dict[str, dict[str, Any]]:
    entries = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            entries[entry["key"]] = entry
    return entries


class ReplayProvider:
    """Answers only from a recording. Counts what it served; never calls a network."""

    def __init__(self, entries: dict[str, dict[str, Any]], name: str = "google") -> None:
        self._entries = entries
        self.name = name
        self.served = 0
        self.misses: list[str] = []

    @classmethod
    def from_file(cls, path: Path, name: str = "google") -> "ReplayProvider":
        return cls(load_recording(path), name)

    def generate_json(
        self,
        *,
        model: str,
        system: str | None,
        messages: Sequence[Message],
        json_schema: dict[str, Any],
        timeout: float,
    ) -> ProviderTextResponse:
        key = generation_key(model, system, messages, json_schema)
        entry = self._entries.get(key)
        if entry is None or entry["op"] != "generate":
            self.misses.append(key)
            raise StaleRecordingError(key, f"a {model} generation ({(system or '')[:40]!r})")
        self.served += 1
        usage = entry.get("usage") or {}
        return ProviderTextResponse(entry["text"], ProviderUsage(**usage) if usage else None)

    def embed(
        self,
        *,
        model: str,
        texts: Sequence[str],
        titles: Sequence[str | None] | None,
        input_type: str,
        output_dimension: int,
        timeout: float,
    ) -> ProviderEmbeddingResponse:
        key = embedding_key(model, texts, titles, input_type, output_dimension)
        entry = self._entries.get(key)
        if entry is None or entry["op"] != "embed":
            self.misses.append(key)
            raise StaleRecordingError(key, f"a {model} embedding of {len(texts)} text(s)")
        self.served += 1
        return ProviderEmbeddingResponse(entry["vectors"])
