"""The pure-Python similarity fallback must be numerically IDENTICAL to
RapidFuzz, not merely close: the identity-resolution thresholds
(_TOKEN_EQUIV_FLOOR = 85, the tier score floors) were calibrated against
RapidFuzz's values, so any drift would silently change linkage decisions
depending on whether an optional binary wheel happened to install.

When RapidFuzz is absent these comparison tests SKIP rather than pass, so a
green run never implies an equivalence that was not actually checked.
"""

from __future__ import annotations

import random

import pytest

from nijsetu.translit.similarity import (
    HAVE_RAPIDFUZZ,
    _ratio_py,
    _lcs_length,
    ratio,
    token_sort_ratio,
)

requires_rapidfuzz = pytest.mark.skipif(
    not HAVE_RAPIDFUZZ, reason="RapidFuzz not installed: nothing to compare the fallback against"
)


def _py_token_sort_ratio(a: str, b: str) -> float:
    return _ratio_py(" ".join(sorted(a.split())), " ".join(sorted(b.split())))


def test_lcs_length_is_symmetric_and_bounded():
    for a, b in [("", ""), ("a", ""), ("abc", "abc"), ("abcde", "ace"), ("arif", "aarif")]:
        n = _lcs_length(a, b)
        assert n == _lcs_length(b, a)
        assert 0 <= n <= min(len(a), len(b))


def test_identical_and_empty_strings_score_100():
    assert _ratio_py("", "") == 100.0
    assert _ratio_py("mohammad arif", "mohammad arif") == 100.0
    assert ratio("", "") == 100.0


def test_disjoint_strings_score_zero():
    assert _ratio_py("abc", "xyz") == 0.0


def test_token_sort_ignores_token_order():
    assert _py_token_sort_ratio("mohammad arif", "arif mohammad") == 100.0
    assert token_sort_ratio("mohammad arif", "arif mohammad") == 100.0


@requires_rapidfuzz
def test_fallback_ratio_matches_rapidfuzz_on_randomised_corpus():
    from rapidfuzz import fuzz

    rng = random.Random(20261126)
    alphabet = "abcdefgh "
    for _ in range(3000):
        a = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 14)))
        b = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 14)))
        assert _ratio_py(a, b) == pytest.approx(fuzz.ratio(a, b), abs=1e-9), (a, b)


@requires_rapidfuzz
def test_fallback_token_sort_matches_rapidfuzz_on_randomised_corpus():
    from rapidfuzz import fuzz

    rng = random.Random(20261127)
    for _ in range(3000):
        def make() -> str:
            return " ".join(
                "".join(rng.choice("abcd") for _ in range(rng.randint(1, 6)))
                for _ in range(rng.randint(0, 4))
            )

        a, b = make(), make()
        assert _py_token_sort_ratio(a, b) == pytest.approx(fuzz.token_sort_ratio(a, b), abs=1e-9), (a, b)


@requires_rapidfuzz
def test_fallback_matches_rapidfuzz_on_real_indic_name_variants():
    """The cases the thresholds actually exist for: cross-script
    transliteration variants, honorific/initial forms and genuinely
    different names must all land on the same number either way."""
    from rapidfuzz import fuzz

    pairs = [
        ("arif", "aarif"),
        ("mohammad arif", "mohd arif"),
        ("mohammad", "muhammad"),
        ("shahid", "sahid"),
        ("ramesh kumar", "ramesh chandra"),
        ("reena nishad", "rina nishad"),
        ("sukhwinder", "sukhvinder"),
        ("kavita pandey", "kavita pande"),
        ("ramesh", "ramesh"),
        ("geeta chauhan", "sita chauhan"),
    ]
    for a, b in pairs:
        assert _ratio_py(a, b) == pytest.approx(fuzz.ratio(a, b), abs=1e-9), (a, b)
        assert _py_token_sort_ratio(a, b) == pytest.approx(fuzz.token_sort_ratio(a, b), abs=1e-9), (a, b)


def test_public_helpers_agree_with_the_active_backend():
    """`ratio`/`token_sort_ratio` must dispatch consistently: whichever
    backend is active, the two public helpers and the Python reference agree
    whenever RapidFuzz is unavailable, and never disagree about ordering."""
    pairs = [("arif", "aarif"), ("ramesh kumar", "ramesh chandra"), ("a", "b")]
    for a, b in pairs:
        assert 0.0 <= ratio(a, b) <= 100.0
        assert 0.0 <= token_sort_ratio(a, b) <= 100.0
    # A closer pair must never score below a more distant one.
    assert ratio("arif", "aarif") > ratio("ramesh kumar", "ramesh chandra")
