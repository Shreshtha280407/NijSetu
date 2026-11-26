"""Cross-script name normalization and comparison keys.

Produces, for a raw name string: a Unicode-normalized comparison form, a
Devanagari->Latin transliteration when needed, an alias-family-normalized
form (Mohammad/Mohd/Md/Muhammad -> one token), a vowel-collapsed form, and a
coarse consonant skeleton. Skeleton/vowel-collapsed equality is a candidate
signal only -- never treated as proof of identity by callers.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from .similarity import token_sort_ratio as _token_sort_ratio

from .devanagari import contains_devanagari, transliterate as translit_devanagari

HONORIFIC_TOKENS = frozenset({
    "dr", "dr.", "mr", "mr.", "mrs", "mrs.", "ms", "ms.", "smt", "smt.",
    "shri", "shree", "sahab", "sahib", "saheb", "ji", "pt", "pt.",
    "s/o", "d/o", "w/o", "c/o",
})

# Treated as a possible name component, never globally stripped (blueprint requirement).
RETAINED_NAME_TOKENS = frozenset({"devi"})

_MOHAMMAD_VARIANTS = frozenset({
    "mohammad", "mohammed", "mohamed", "muhammad", "mohamad", "mohd", "mohd.", "md", "md.",
})
MOHAMMAD_CANONICAL = "mohammad"

_VOWELS = "aeiou"
_VOWEL_RUN = re.compile(f"[{_VOWELS}]+")
_REPEAT = re.compile(r"(.)\1+")
_NON_ALNUM = re.compile(r"[^a-z0-9\s]")


@lru_cache(maxsize=4096)
def unicode_compare_form(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().strip()


@lru_cache(maxsize=4096)
def to_latin(text: str) -> str:
    """Devanagari -> Latin when the script is detected, else pass through lowercased."""
    base = unicode_compare_form(text)
    if contains_devanagari(base):
        return translit_devanagari(base)
    return base


def strip_honorifics(tokens: tuple[str, ...]) -> tuple[str, ...]:
    # RETAINED_NAME_TOKENS (e.g. "devi") is never in HONORIFIC_TOKENS; kept as a
    # visible guard so a future honorific addition can't silently swallow it.
    return tuple(
        t for t in tokens
        if t in RETAINED_NAME_TOKENS or t.rstrip(".") not in HONORIFIC_TOKENS
    )


def normalize_alias_family(token: str) -> str:
    """Maps a single name token to its canonical spelling family, else unchanged."""
    bare = token.rstrip(".").lower()
    if bare in _MOHAMMAD_VARIANTS:
        return MOHAMMAD_CANONICAL
    return token.lower()


@lru_cache(maxsize=4096)
def vowel_collapsed(text: str) -> str:
    """Neutralizes vowel quality while preserving consonant/syllable structure."""
    latin = to_latin(text)
    cleaned = _NON_ALNUM.sub("", latin)
    return _VOWEL_RUN.sub("V", cleaned)


@lru_cache(maxsize=4096)
def consonant_skeleton(text: str) -> str:
    """Coarse candidate-generation key: vowels removed, doubled letters collapsed."""
    latin = to_latin(text)
    cleaned = _NON_ALNUM.sub("", latin)
    no_vowels = re.sub(f"[{_VOWELS}]", "", cleaned)
    return _REPEAT.sub(r"\1", no_vowels)


def tokenize_name(raw_name: str) -> tuple[str, ...]:
    latin = to_latin(raw_name)
    raw_tokens = tuple(t for t in re.split(r"[\s,]+", latin) if t)
    canonical = tuple(normalize_alias_family(t) for t in raw_tokens)
    return strip_honorifics(canonical)


def token_sequence_similarity(name_a: str, name_b: str) -> float:
    """Final-comparison similarity on full normalized token sequences (0-100)."""
    tokens_a = " ".join(tokenize_name(name_a))
    tokens_b = " ".join(tokenize_name(name_b))
    if not tokens_a or not tokens_b:
        return 0.0
    return _token_sort_ratio(tokens_a, tokens_b)
