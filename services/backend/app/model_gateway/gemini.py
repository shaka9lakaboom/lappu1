"""Google AI (Gemini API) provider adapter - the only module that imports the SDK.

Frozen V1 choices (architecture §14.1): structured generation on
`gemini-3.7-flash`, embeddings on `gemini-embedding-2` at 768 dimensions.
The API key is server-only and is never logged.
"""

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

import httpx
from google import genai
from google.genai import errors, types

from app.model_gateway.types import (
    Message,
    ProviderEmbeddingResponse,
    ProviderError,
    ProviderTextResponse,
    ProviderUsage,
)

logger = logging.getLogger("skillmirror.model_gateway.gemini")

# gemini-embedding-2 takes the retrieval task as a text prefix instead of task_type
# (Gemini API embeddings guide). Older embedding models use task_type.
_QUERY_PREFIX = "task: search result | query: "

ThinkingLevel = Literal["minimal", "low", "medium", "high"]


class GeminiProvider:
    name = "google"

    def __init__(
        self,
        api_key: str,
        *,
        thinking_level: ThinkingLevel = "low",
        thinking_levels: Mapping[str, ThinkingLevel] | None = None,
        client: Any | None = None,
    ) -> None:
        self._client = client or genai.Client(api_key=api_key)
        self._thinking_level = thinking_level
        # Per-model overrides (e.g. a routine Flash-Lite model), else the default level.
        self._thinking_levels = dict(thinking_levels or {})

    def generate_json(
        self,
        *,
        model: str,
        system: str | None,
        messages: Sequence[Message],
        json_schema: dict[str, Any],
        timeout: float,
    ) -> ProviderTextResponse:
        contents = [
            types.Content(
                role="model" if m.role == "assistant" else "user",
                parts=[types.Part.from_text(text=m.content)],
            )
            for m in messages
        ]
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=json_schema,
            thinking_config=types.ThinkingConfig(
                thinking_level=self._thinking_levels.get(model, self._thinking_level).upper()
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            http_options=types.HttpOptions(timeout=int(timeout * 1000)),
        )
        response = self._call(
            lambda: self._client.models.generate_content(
                model=model, contents=contents, config=config
            )
        )
        text = response.text
        if not text:
            reason = None
            if response.candidates:
                reason = getattr(response.candidates[0], "finish_reason", None)
            raise ProviderError("failed", "EMPTY_RESPONSE", f"no text returned ({reason})")
        usage = response.usage_metadata
        output_tokens = None
        if usage is not None and usage.candidates_token_count is not None:
            output_tokens = usage.candidates_token_count + (usage.thoughts_token_count or 0)
        return ProviderTextResponse(
            text=text,
            usage=ProviderUsage(
                input_tokens=usage.prompt_token_count if usage else None,
                output_tokens=output_tokens,
                total_tokens=usage.total_token_count if usage else None,
            ),
        )

    def embed(
        self,
        *,
        model: str,
        texts: Sequence[str],
        titles: Sequence[str | None] | None,
        input_type: Literal["query", "document"],
        output_dimension: int,
        timeout: float,
    ) -> ProviderEmbeddingResponse:
        prefixed = model.startswith("gemini-embedding-2")
        if prefixed:
            formatted = [
                _QUERY_PREFIX + text
                if input_type == "query"
                else f"title: {(titles[i] if titles else None) or 'none'} | text: {text}"
                for i, text in enumerate(texts)
            ]
            config = types.EmbedContentConfig(
                output_dimensionality=output_dimension,
                http_options=types.HttpOptions(timeout=int(timeout * 1000)),
            )
        else:
            formatted = list(texts)
            config = types.EmbedContentConfig(
                output_dimensionality=output_dimension,
                task_type="RETRIEVAL_QUERY" if input_type == "query" else "RETRIEVAL_DOCUMENT",
                http_options=types.HttpOptions(timeout=int(timeout * 1000)),
            )
        # Each text in its own Content: passing bare strings makes gemini-embedding-2
        # return ONE aggregated embedding for all of them.
        contents = [types.Content(parts=[types.Part.from_text(text=t)]) for t in formatted]
        response = self._call(
            lambda: self._client.models.embed_content(model=model, contents=contents, config=config)
        )
        vectors = [list(e.values or []) for e in (response.embeddings or [])]
        return ProviderEmbeddingResponse(vectors=vectors)

    @staticmethod
    def _call(fn: Any) -> Any:
        try:
            return fn()
        except errors.ClientError as exc:
            code = getattr(exc, "code", None)
            if code == 429:
                raise ProviderError(
                    "rate_limited", "HTTP_429", "rate limited", retry_after=_retry_after(exc)
                ) from exc
            if code in (401, 403):
                raise ProviderError("unavailable", f"HTTP_{code}", "credentials rejected") from exc
            if code == 404:
                raise ProviderError("unavailable", "HTTP_404", "model not found") from exc
            raise ProviderError("failed", f"HTTP_{code}", _safe_message(exc)) from exc
        except errors.ServerError as exc:
            code = getattr(exc, "code", None)
            kind = "unavailable" if code == 503 else "failed"
            raise ProviderError(
                kind, f"HTTP_{code}", _safe_message(exc), retry_after=_retry_after(exc)
            ) from exc
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise ProviderError("timeout", "TIMEOUT", "provider call timed out") from exc
        except httpx.TransportError as exc:
            raise ProviderError("unavailable", "TRANSPORT", type(exc).__name__) from exc


_RETRY_IN = re.compile(r"retry in ([0-9]+(?:[.][0-9]+)?)s", re.IGNORECASE)


def _retry_after(exc: Exception) -> float | None:
    """The server's RetryInfo delay (e.g. "27s"), or a "retry in 27.4s" hint."""
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        for item in (details.get("error") or {}).get("details") or []:
            delay = item.get("retryDelay") if isinstance(item, dict) else None
            if isinstance(delay, str) and delay.endswith("s"):
                try:
                    return float(delay[:-1])
                except ValueError:
                    pass
    match = _RETRY_IN.search(str(getattr(exc, "message", "") or ""))
    return float(match.group(1)) if match else None


def _safe_message(exc: Exception) -> str:
    # SDK error messages carry the API status text; they never include the key.
    return str(getattr(exc, "message", None) or exc)[:500]
