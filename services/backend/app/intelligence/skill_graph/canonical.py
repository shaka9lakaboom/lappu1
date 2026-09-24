"""Canonical skill keys and slugs (architecture §8, §16 "Visualization / Visualisation").

`skill_key()` is the canonicalization key stored in `skill_nodes.normalized_name`
and `skill_aliases.normalized_alias`, both unique. Two names with the same key
are the same skill, so capitalization, punctuation, accents, simple plurals and
British/American spelling variants can never create a second identity.

The rules are deliberately conservative: they only ever fold spellings that
differ by a regular variant, and they are applied identically to both sides of
every comparison.
"""

import re
import unicodedata

_SPLIT = re.compile(r"[^\w+#]+|_")
_ALPHA = re.compile(r"^[a-z]+$")

# Applied in order to alphabetic tokens of length >= 4 (plural folding).
_PLURAL_RULES: tuple[tuple[str, str], ...] = (
    ("sses", "ss"),
    ("ies", "y"),
    ("xes", "x"),
    ("ches", "ch"),
    ("shes", "sh"),
)
_KEEP_TRAILING_S = ("ss", "us", "is", "os")

# British -> American spelling folds, applied to alphabetic tokens of length >= 5.
_SPELLING_RULES: tuple[tuple[str, str], ...] = (
    ("isation", "ization"),
    ("ising", "izing"),
    ("ised", "ized"),
    ("ise", "ize"),
    ("ysing", "yzing"),
    ("ysed", "yzed"),
    ("yse", "yze"),
    ("elling", "eling"),
    ("elled", "eled"),
)


def _strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _fold_token(token: str) -> str:
    if not _ALPHA.match(token) or len(token) < 4:
        return token
    for suffix, replacement in _PLURAL_RULES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            token = token[: -len(suffix)] + replacement
            break
    else:
        if token.endswith("s") and not token.endswith(_KEEP_TRAILING_S):
            token = token[:-1]
    if len(token) >= 5:
        for suffix, replacement in _SPELLING_RULES:
            if token.endswith(suffix):
                token = token[: -len(suffix)] + replacement
                break
    return token


def skill_key(name: str) -> str:
    """Canonical comparison key for a skill name or alias."""
    text = _strip_accents(unicodedata.normalize("NFKC", name)).lower().replace("&", " and ")
    tokens = [_fold_token(t) for t in _SPLIT.split(text) if t]
    return " ".join(tokens)[:160].strip()


def slugify(name: str, max_length: int = 100) -> str:
    text = _strip_accents(unicodedata.normalize("NFKC", name)).lower()
    text = text.replace("c++", "cpp").replace("c#", "csharp").replace("+", " plus ")
    text = text.replace("#", " sharp ").replace("&", " and ")
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    slug = slug[:max_length].strip("-")
    return slug or "skill"
