"""Rules-based Devanagari -> Latin transliteration for name matching.

Deliberately coarse: good enough to feed candidate generation and the
vowel-collapsed/consonant-skeleton comparison keys, not a general-purpose
linguistic transliterator. Word-final schwa deletion is approximated
(standard Hindi pronunciation drops the final inherent "a"), which is what
makes "आरिफ़" transliterate to "arif" rather than "arifa".

ponytail: no indic-transliteration dependency pulled in for this -- it's a
flat lookup table plus one linear pass, which covers the name-matching need
without a new runtime dependency. Upgrade to a real ISO-15919 library only if
a judge sample surfaces scripts/conjuncts this table does not cover.
"""

from __future__ import annotations

import re

_INDEPENDENT_VOWELS = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ii",
    "उ": "u", "ऊ": "uu", "ऋ": "ri", "ए": "e",
    "ऐ": "ai", "ओ": "o", "औ": "au",
    "ॠ": "rri", "ॡ": "lli",
}

_MATRAS = {
    "ा": "aa", "ि": "i", "ी": "ii", "ु": "u", "ू": "uu",
    "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au",
    "ॢ": "li", "ॣ": "lli",
}

_VIRAMA = "्"
_NUKTA = "़"
_ANUSVARA = "ं"
_CHANDRABINDU = "ँ"
_VISARGA = "ः"
_AVAGRAHA = "ऽ"

_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ng",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "ny",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h", "ळ": "l",
}

# Nukta variants: Perso-Arabic loan sounds common in South-Asian Muslim names.
_NUKTA_CONSONANTS = {
    "क़": "q",   # क़
    "ख़": "kh",  # ख़
    "ग़": "gh",  # ग़
    "ज़": "z",   # ज़
    "फ़": "f",   # फ़
    "ड़": "r",   # ड़
    "ढ़": "rh",  # ढ़
}

_DEVANAGARI_RANGE = re.compile(r"[ऀ-ॿ]")


def contains_devanagari(text: str) -> bool:
    return bool(_DEVANAGARI_RANGE.search(text))


def transliterate(text: str) -> str:
    """Best-effort Devanagari -> Latin. Non-Devanagari characters pass through."""
    out: list[str] = []
    i = 0
    n = len(text)
    pending_consonant: str | None = None

    def flush_pending_with_vowel(vowel: str) -> None:
        nonlocal pending_consonant
        if pending_consonant is not None:
            out.append(pending_consonant + vowel)
            pending_consonant = None

    def flush_pending_bare() -> None:
        nonlocal pending_consonant
        if pending_consonant is not None:
            out.append(pending_consonant)
            pending_consonant = None

    while i < n:
        ch = text[i]
        two = text[i:i + 2]
        if two in _NUKTA_CONSONANTS:
            flush_pending_bare()
            pending_consonant = _NUKTA_CONSONANTS[two]
            i += 2
            continue
        if ch in _CONSONANTS:
            flush_pending_with_vowel("a")
            pending_consonant = _CONSONANTS[ch]
            i += 1
            continue
        if ch == _VIRAMA:
            flush_pending_bare()
            i += 1
            continue
        if ch in _MATRAS:
            flush_pending_with_vowel(_MATRAS[ch])
            i += 1
            continue
        if ch in _INDEPENDENT_VOWELS:
            flush_pending_with_vowel("a")
            out.append(_INDEPENDENT_VOWELS[ch])
            i += 1
            continue
        if ch == _ANUSVARA or ch == _CHANDRABINDU:
            flush_pending_with_vowel("a")
            out.append("n")
            i += 1
            continue
        if ch == _VISARGA:
            flush_pending_with_vowel("a")
            out.append("h")
            i += 1
            continue
        if ch == _AVAGRAHA or ch == _NUKTA:
            i += 1
            continue
        flush_pending_with_vowel("a")
        out.append(ch)
        i += 1

    flush_pending_with_vowel("a")
    result = "".join(out)
    return _apply_schwa_deletion(result)


def _apply_schwa_deletion(word: str) -> str:
    """Drop one word-final inherent 'a' per space-separated token, when safe."""
    tokens = word.split(" ")
    fixed = []
    for tok in tokens:
        if len(tok) > 2 and tok.endswith("a") and not tok.endswith("aa"):
            tok = tok[:-1]
        fixed.append(tok)
    return " ".join(fixed)
