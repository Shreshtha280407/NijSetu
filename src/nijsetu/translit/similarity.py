"""String similarity with a pure-Python fallback, so the package has no
mandatory binary dependency.

NijSetu uses exactly two RapidFuzz functions -- `fuzz.ratio` and
`fuzz.token_sort_ratio` -- for name comparison. RapidFuzz is a compiled
extension, so a wheel built for one CPython ABI (cp311) cannot install on
another (cp314). The organizer's `setup` step does have network access
(docs/SANDBOX.md), so a normal `pip install .` resolves the right wheel; but
our own offline-install regression test, and any evaluator whose interpreter
we did not anticipate, must not be able to fail on an ABI mismatch for a
feature this small.

So RapidFuzz stays the preferred implementation (it is C-fast and is what the
thresholds were calibrated against) and this module falls back to an exact
Python reimplementation when the import fails. "Exact" is a tested claim, not
an approximation: `fuzz.ratio` is the indel-normalised similarity

    100 * (1 - indel_distance / (len(a) + len(b)))

where `indel_distance = len(a) + len(b) - 2 * |LCS(a, b)|`, and
`token_sort_ratio` is the same measure over whitespace-sorted tokens.
Equivalence to RapidFuzz is asserted over randomised corpora in
tests/test_similarity_fallback.py, which is skipped (not silently passed)
when RapidFuzz is absent and so cannot be compared against.
"""

from __future__ import annotations

try:  # pragma: no cover - exercised by whichever branch the environment has
    from rapidfuzz import fuzz as _fuzz

    HAVE_RAPIDFUZZ = True
except ImportError:  # pragma: no cover
    _fuzz = None
    HAVE_RAPIDFUZZ = False


def _lcs_length(a: str, b: str) -> int:
    """Longest common subsequence length, O(len(a) * len(b)) time and
    O(min(len(a), len(b))) space. Inputs here are single names or short
    token sequences (tens of characters), never documents."""
    if not a or not b:
        return 0
    if len(a) < len(b):  # keep the rolling row on the shorter side
        a, b = b, a
    previous = [0] * (len(b) + 1)
    for char_a in a:
        current = [0]
        for j, char_b in enumerate(b):
            current.append(previous[j] + 1 if char_a == char_b else max(current[j], previous[j + 1]))
        previous = current
    return previous[-1]


def _ratio_py(a: str, b: str) -> float:
    total = len(a) + len(b)
    if total == 0:  # two empty strings are identical, matching RapidFuzz
        return 100.0
    indel_distance = total - 2 * _lcs_length(a, b)
    return (1.0 - indel_distance / total) * 100.0


def ratio(a: str, b: str) -> float:
    """Indel-normalised similarity in [0, 100]."""
    if HAVE_RAPIDFUZZ:
        return _fuzz.ratio(a, b)
    return _ratio_py(a, b)


def token_sort_ratio(a: str, b: str) -> float:
    """`ratio` after sorting each side's whitespace-separated tokens, so
    "mohammad arif" and "arif mohammad" compare as the same name."""
    if HAVE_RAPIDFUZZ:
        return _fuzz.token_sort_ratio(a, b)
    return _ratio_py(" ".join(sorted(a.split())), " ".join(sorted(b.split())))
