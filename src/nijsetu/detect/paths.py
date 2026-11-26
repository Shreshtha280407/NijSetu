"""Path-shaped values: detect them, and decide whether they carry PII.

A hospital export habitually records where it came from:

    "source_path": "D:\\\\HIS\\\\exports\\\\opd\\\\Anita_Sharma_010565\\\\visits_2026_02.json"

Scanned as prose this leaks, and it leaked measurably: 14 of 16
residual-leak quarantines on the practice data were exactly this field. Two
reasons compound:

  * `_` is a word character, so a context-free regex boundary (`\\b[6-9]\\d{9}\\b`,
    a gazetteer name lookup) finds nothing between "Anita" and "Sharma" and
    nothing between "Sharma" and "010565" -- the directory name is one
    undelimited token to every detector;
  * even with boundaries restored, only SOME components are recognisable. The
    surname matched the gazetteer and was redacted; the given name and the
    six-digit hospital number were published. A partially-redacted path is
    still a leak, and worse, it looks handled.

So the decision here is per-VALUE, not per-span: split the path into its
components, restore word boundaries inside each one, and ask the ordinary
detectors whether any component carries PII. If one does, the caller replaces
the WHOLE value. That is safe by construction -- no residue is possible -- and
costs nothing clinically, because a provenance path holds no clinical
information. A path whose components are all innocuous ("exports/opd/
visits_2026_02.json") is left exactly as it was, so the field keeps its
meaning wherever it is harmless.
"""

from __future__ import annotations

import re

from ..schema import FieldRole, SourceCoordinate

# Length-PRESERVING separator normalisation. Each separator becomes exactly one
# space, so every offset in the normalised copy is still valid on the original
# -- the same property the identifier detectors rely on for Devanagari digits.
# A regex substitution of `[_-]+` would collapse runs and shift offsets.
_SEPARATOR_TRANSLATION = str.maketrans({"_": " ", "-": " ", "\\": " ", "/": " ", ".": " "})

# Something is treated as a path only on STRONG evidence. Clinical data is full
# of slash-bearing values that are not paths at all -- "g/dL", "112/78",
# "OPD/26/02/00085", "1-0-1" -- and misreading one as a path would discard a
# real clinical or identifier value.
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_UNC_PREFIX = re.compile(r"^\\\\[^\\]")
_POSIX_ABSOLUTE = re.compile(r"^/[^/\s]+/")
_HAS_BACKSLASH_SEPARATOR = re.compile(r"[^\\]\\[^\\]")
_ENDS_WITH_FILE_EXTENSION = re.compile(r"[\\/][^\\/\s]+\.[A-Za-z0-9]{2,5}$")

# Replacement for a path-shaped value whose components carry PII. A string
# (not None) so a JSON value keeps its type, per SPEC.md's "JSON stays JSON
# with the same structure and value types".
REDACTED_PATH = "[REDACTED_PATH]"

_PII_PATH_ROLES = frozenset({
    FieldRole.PATIENT_NAME,
    FieldRole.RELATIVE_NAME,
    FieldRole.PATIENT_IDENTIFIER,
    FieldRole.CONTACT,
    FieldRole.FINE_ADDRESS,
})

# A bare numeric run inside a path component is a hospital-local record number
# far more often than anything else ("Anita_Sharma_010565", "Shahid_NA_010607").
# It carries no label to anchor a labelled-identifier detector and no checksum
# to confirm a national scheme, so it is judged structurally: a long digit run
# sitting in a directory/file name alongside other components is treated as an
# identifier. The bound is deliberately not 4 (a bare year, "visits_2026_02",
# must stay innocuous).
_MIN_BARE_ID_DIGITS = 5
_BARE_ID_RUN = re.compile(rf"(?<!\d)\d{{{_MIN_BARE_ID_DIGITS},}}(?!\d)")
# A pure date-like component ("2026", "2026_02", "20260215") is provenance, not
# an identifier -- excluded so an export timestamp does not condemn the path.
_DATE_LIKE_COMPONENT = re.compile(r"^(?:19|20)\d{2}(?:[\s]?(?:0[1-9]|1[0-2]))?(?:[\s]?(?:0[1-9]|[12]\d|3[01]))?$")


def looks_like_path(value: str) -> bool:
    """True only on strong structural evidence that `value` is a file path."""
    if not value or len(value) > 4096 or "\n" in value:
        return False
    return bool(
        _WINDOWS_DRIVE.match(value)
        or _UNC_PREFIX.match(value)
        or _POSIX_ABSOLUTE.match(value)
        or _HAS_BACKSLASH_SEPARATOR.search(value)
        or _ENDS_WITH_FILE_EXTENSION.search(value)
    )


def path_components(value: str) -> list[str]:
    return [part for part in re.split(r"[\\/]+", value) if part]


def _component_carries_pii(component: str, detect_in_text) -> bool:
    normalized = component.translate(_SEPARATOR_TRANSLATION)
    base = SourceCoordinate(file_no=0, file_path="", record_no=-1, path="path_component")
    if any(f.role in _PII_PATH_ROLES for f in detect_in_text(normalized, base)):
        return True
    for match in _BARE_ID_RUN.finditer(normalized):
        # Re-read the run together with its neighbours in the normalised
        # component: a standalone export date is provenance, a long digit run
        # welded to a name is a record number.
        if not _DATE_LIKE_COMPONENT.match(normalized.strip()):
            return True
    return False


def path_carries_pii(value: str, detect_in_text) -> bool:
    """True when ANY component of a path-shaped value carries personal data.

    `detect_in_text` is passed in rather than imported to keep this module
    free of a circular dependency on the detector package that uses it.
    """
    return any(_component_carries_pii(component, detect_in_text) for component in path_components(value))
