"""Scope-level day-first/month-first convention inference for numeric dates.

`dates.parse_flexible_date` deliberately refuses to guess between the
`dd/mm/yyyy` and `mm/dd/yyyy` conventions when a single value parses validly
under both (e.g. "03/02/2026" is 3 Feb or 2 Mar). That is the right call for
one isolated value -- it genuinely carries no information -- but it is the
wrong unit of decision: a date never arrives alone. It arrives in a CSV
column, a JSON key or a document written by one exporter that used ONE
convention throughout, and that scope usually contains at least one value
which is only valid one way ("24/09/2026" cannot be month-first).

So the convention is resolved per *scope*, cheapest and most authoritative
evidence first:

  1. an explicit label the source itself prints -- "DOB (MM/DD/YYYY)",
     "Registered (dd/mm/yyyy)" (the legacy cp1252 export does exactly this);
  2. a value inside the same scope that only one convention can parse
     (a component > 12 in the day or month position);
  3. the same evidence widened to the whole file, then the whole corpus;
  4. a declared fallback.

Step 4 is a utility decision, not a guess dressed up as knowledge: every
released date is shifted by a per-patient secret offset anyway, so the
absolute calendar value is already deliberately perturbed, while the
*interval* between two dates in one record (length of stay, the
`utility.temporal` component) is preserved exactly as long as the convention
is applied CONSISTENTLY within the scope. Dropping the field instead -- the
previous behaviour -- destroys the interval and the record with it. The
fallback is therefore recorded with an explicit reason code so a reader can
see which dates rest on it.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

DAY_FIRST = "DMY"
MONTH_FIRST = "MDY"

# A value shaped like "<a><sep><b><sep><yyyy>" -- the only family that can be
# ambiguous. ISO (yyyy-mm-dd), year-month, bare year and textual-month forms
# are unambiguous by shape and never reach this module.
_NUMERIC_TRIPLE = re.compile(r"^\s*(\d{1,2})([/-])(\d{1,2})\2(\d{4})\s*$")

# An explicit convention the source prints next to the column/field name.
# Matched on the label, never on data.
_LABEL_DAY_FIRST = re.compile(r"\bd{1,2}\s*[/\-.]\s*m{1,2}\s*[/\-.]\s*y{2,4}\b", re.I)
_LABEL_MONTH_FIRST = re.compile(r"\bm{1,2}\s*[/\-.]\s*d{1,2}\s*[/\-.]\s*y{2,4}\b", re.I)


@dataclass(frozen=True)
class ConventionDecision:
    """`convention` is None only when the caller asked for no fallback."""

    convention: str | None
    reason: str  # auditable provenance of the decision


def convention_from_label(label: str | None) -> str | None:
    """A convention the source explicitly declares in a column/field label.

    Checked month-first before day-first: "mm/dd/yyyy" would also satisfy a
    naive day-first reading of its own first component.
    """
    if not label:
        return None
    if _LABEL_MONTH_FIRST.search(label):
        return MONTH_FIRST
    if _LABEL_DAY_FIRST.search(label):
        return DAY_FIRST
    return None


def forced_convention(value: str) -> str | None:
    """The only convention under which `value` is a valid date, if exactly one.

    A component in 13..31 can only be a day, which pins the convention by
    its position. Returns None when both readings are valid (no information)
    or neither is (not our problem -- `parse_flexible_date` reports it).
    """
    m = _NUMERIC_TRIPLE.match(value)
    if m is None:
        return None
    first, second = int(m.group(1)), int(m.group(3))
    first_is_day_only = 12 < first <= 31
    second_is_day_only = 12 < second <= 31
    if first_is_day_only and not second_is_day_only:
        return DAY_FIRST
    if second_is_day_only and not first_is_day_only:
        return MONTH_FIRST
    return None


def infer_from_values(values: object) -> tuple[str | None, int, int]:
    """Returns (convention, n_day_first_votes, n_month_first_votes).

    A scope whose values disagree (both conventions forced by different
    values) is genuinely inconsistent -- the exporter mixed formats, or the
    scope was mis-grouped. The majority wins and the disagreement is visible
    in the returned counts, which the caller records; it is never silently
    collapsed.
    """
    votes: Counter[str] = Counter()
    for value in values:  # type: ignore[union-attr]
        forced = forced_convention(value)
        if forced is not None:
            votes[forced] += 1
    day, month = votes[DAY_FIRST], votes[MONTH_FIRST]
    if day == 0 and month == 0:
        return None, 0, 0
    if day == month:
        # A dead tie between two equally-attested conventions is not
        # resolvable from the data; defer to the caller's wider scope.
        return None, day, month
    return (DAY_FIRST if day > month else MONTH_FIRST), day, month


class DateConventionResolver:
    """Resolves one scope at a time against progressively wider evidence.

    Scopes are keyed `(file_no, field_path)`; `field_path` is a CSV column
    name, a JSON pointer-ish key path or a free-text body marker -- whatever
    the detection layer already used as `Feature.source.path`, so no new
    notion of "where a value came from" is introduced here.
    """

    def __init__(self, default_convention: str | None = DAY_FIRST):
        self._scope_values: dict[tuple[int, str], list[str]] = {}
        self._scope_labels: dict[tuple[int, str], str] = {}
        self._decisions: dict[tuple[int, str], ConventionDecision] = {}
        self._default = default_convention

    def observe(self, file_no: int, field_path: str, value: str, label: str | None = None) -> None:
        key = (file_no, field_path)
        self._scope_values.setdefault(key, []).append(value)
        if label and key not in self._scope_labels:
            self._scope_labels[key] = label

    def _file_values(self, file_no: int) -> list[str]:
        return [v for (fno, _), vals in self._scope_values.items() if fno == file_no for v in vals]

    def _corpus_values(self) -> list[str]:
        return [v for vals in self._scope_values.values() for v in vals]

    def stamp(self, file_no: int, features: object) -> None:
        """Apply this scope's resolved convention to DATE features.

        Needed for RE-DETECTED features as much as for original ones: the
        clinical-invariant check re-parses the staged output with a fresh
        detection pass, and those Features have never been through
        `observe`/`resolve`. Leaving them unstamped makes a correctly
        shifted, correctly re-serialized date look unparseable, which reads
        as "the transform lost an interval date" and holds a record that is
        in fact perfectly fine.
        """
        from ..schema import FieldRole

        for f in features:  # type: ignore[union-attr]
            if f.role is FieldRole.DATE and getattr(f, "date_convention", None) is None:
                f.date_convention = self.resolve(file_no, f.source.path or "").convention

    def resolve(self, file_no: int, field_path: str) -> ConventionDecision:
        key = (file_no, field_path)
        cached = self._decisions.get(key)
        if cached is not None:
            return cached
        decision = self._resolve_uncached(key)
        self._decisions[key] = decision
        return decision

    def _resolve_uncached(self, key: tuple[int, str]) -> ConventionDecision:
        file_no, field_path = key

        # 1. The source said so itself -- in the label, never inferred from data.
        declared = convention_from_label(self._scope_labels.get(key) or field_path)
        if declared is not None:
            return ConventionDecision(declared, f"LABEL_DECLARED_{declared}")

        # 2. A value in this exact scope that only one convention can parse.
        conv, day, month = infer_from_values(self._scope_values.get(key, ()))
        if conv is not None:
            suffix = f"_MIXED_{day}v{month}" if day and month else ""
            return ConventionDecision(conv, f"SCOPE_EVIDENCE_{conv}{suffix}")

        # 3. Same evidence, widened: this file, then the whole corpus. A
        #    sparse column (every value ambiguous) inherits the convention
        #    its own exporter demonstrably used elsewhere.
        conv, day, month = infer_from_values(self._file_values(file_no))
        if conv is not None:
            return ConventionDecision(conv, f"FILE_EVIDENCE_{conv}")
        conv, day, month = infer_from_values(self._corpus_values())
        if conv is not None:
            return ConventionDecision(conv, f"CORPUS_EVIDENCE_{conv}")

        # 4. Declared fallback (see module docstring on why consistency beats
        #    suppression here). `None` preserves the strict hold behaviour.
        if self._default is None:
            return ConventionDecision(None, "NO_EVIDENCE_HELD")
        return ConventionDecision(self._default, f"FALLBACK_{self._default}")
