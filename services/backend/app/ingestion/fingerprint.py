"""Content normalization and event fingerprints (architecture §6.6).

Mirrors normalizeContent / coarseTimestamp / fallbackFingerprintInput in
packages/contracts/src/raw-activity.ts. Both are checked against
packages/contracts/fixtures/fingerprint-vectors.json.

The backend computes these itself; values sent by a client are never trusted.
"""

import hashlib
import re
import unicodedata
from datetime import UTC, datetime

# Spelled out to match the TypeScript implementation exactly (JS and Python
# `\s` differ at the edges), as explicit code points.
WHITESPACE_CODE_POINTS = (
    0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0xA0, 0x1680,
    *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF,
)  # fmt: skip
_WHITESPACE_RUN = re.compile(
    "[" + "".join(re.escape(chr(cp)) for cp in WHITESPACE_CODE_POINTS) + "]+"
)


def normalize_content(text: str) -> str:
    """NFC, every whitespace run collapsed to one space, trimmed."""
    collapsed = _WHITESPACE_RUN.sub(" ", unicodedata.normalize("NFC", text))
    return collapsed.removeprefix(" ").removesuffix(" ")


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def content_hash(text: str) -> str:
    return sha256_hex(normalize_content(text))


def coarse_timestamp(moment: datetime) -> str:
    """UTC hour bucket, "YYYY-MM-DDTHH"."""
    if moment.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H")


def fallback_fingerprint(
    *,
    source_provider: str,
    external_conversation_id: str | None,
    role: str,
    content_text: str,
    occurred_at: datetime | None,
    captured_at: datetime,
) -> str:
    """SHA256(provider | conversation_id | role | normalized_content | coarse_timestamp)."""
    pre_image = "|".join(
        [
            source_provider,
            external_conversation_id or "",
            role,
            normalize_content(content_text),
            coarse_timestamp(occurred_at or captured_at),
        ]
    )
    return sha256_hex(pre_image)
