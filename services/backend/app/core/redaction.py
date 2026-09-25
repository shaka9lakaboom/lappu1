"""Redaction of secrets and personal identifiers in operational text (errors, logs).

Job and model-run errors are exception messages. They should never hold a secret, but a
driver or SDK error can echo a connection string, a request URL with its API key, a bearer
token or an e-mail address. Everything shown to an admin (and every stored job error) goes
through `redact` first; it is a precaution, not the only line of defence.
"""

import re

_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"AIza[0-9A-Za-z_-]{20,}"), "[redacted-api-key]"),
    (re.compile(r"sb_(?:secret|publishable)_[A-Za-z0-9_-]{8,}"), "[redacted-supabase-key]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[redacted-jwt]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [redacted]"),
    (re.compile(r"(?i)\bpostgres(?:ql)?://[^\s'\"]+"), "postgresql://[redacted]"),
    (
        re.compile(r"(?i)([?&](?:key|api_key|apikey|token|access_token)=)[^&\s'\"]+"),
        r"\1[redacted]",
    ),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret)(\s*[=:]\s*)[^\s,;'\"]+"), r"\1\2[redacted]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"), "[redacted-email]"),
)


def redact(text: str | None, max_chars: int | None = None) -> str | None:
    """`text` with secrets and e-mail addresses masked, optionally truncated (with an ellipsis)."""
    if text is None:
        return None
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    if max_chars is not None and len(text) > max_chars:
        text = text[: max(max_chars - 1, 0)] + "…"
    return text
