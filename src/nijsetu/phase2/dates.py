"""Flexible date parsing + format-preserving date shifting.

Parses ISO dates/datetimes (including FHIR partial dates "YYYY"/"YYYY-MM")
and a small set of explicit non-ISO day-first conventions. Ambiguous input
(more than one convention parses to a different result) and unparseable
input ("18 November" with no year) are raised as explicit errors -- callers
must hold/suppress the field, never guess.

Shifting preserves the original precision and, for a full datetime, the
exact original time-of-day/timezone suffix text (only the calendar date
component changes) so shifted output round-trips through the same FHIR/CSV
representation the source used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]")
_YEAR_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_YEAR_RE = re.compile(r"^\d{4}$")

# Explicit non-ISO conventions a CSV/free-text source may use. "%m/%d/%Y" is
# deliberately included alongside "%d/%m/%Y" so a genuinely ambiguous
# day<=12 input (dd/mm vs the US mm/dd convention) is held as ambiguous
# rather than silently guessed one way -- every format that matches must
# agree on the exact resulting date, or the field is held.
_NUMERIC_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d")
_TEXT_FORMATS = ("%d %b %Y", "%d %B %Y")

# Which of the above encode a day-first vs a month-first reading. "%Y/%m/%d"
# is year-first and unambiguous by shape, so it belongs to neither and is
# never filtered out by a convention.
_DAY_FIRST_FORMATS = frozenset({"%d-%m-%Y", "%d/%m/%Y"})
_MONTH_FIRST_FORMATS = frozenset({"%m/%d/%Y"})


class DateParseError(Exception):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ParsedDate:
    value: date
    fmt: str
    precision: str  # "year" | "month" | "day" | "datetime"
    time_suffix: str | None = None  # raw "Txx:xx:xx+05:30" tail, datetime only


def parse_flexible_date(text: str, convention: str | None = None) -> ParsedDate:
    """`convention` is "DMY"/"MDY" (see phase2.date_convention) and applies
    ONLY to the genuinely ambiguous numeric-triple family: it selects between
    two otherwise-equally-valid readings of the same value. It never makes an
    unparseable value parse, never overrides an unambiguous shape (ISO,
    year-month, bare year, textual month, or a value with a component > 12
    that already pins the order), and `None` keeps the strict behaviour of
    raising DATE_AMBIGUOUS.
    """
    text = text.strip()
    if not text:
        raise DateParseError("DATE_EMPTY")

    if _ISO_DATETIME_RE.match(text):
        iso_text = text[:10] + "T" + text[11:] if text[10] == " " else text
        try:
            dt = datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
        except ValueError:
            raise DateParseError("DATE_UNPARSEABLE") from None
        return ParsedDate(dt.date(), "ISO_DATETIME", "datetime", time_suffix=text[10:])

    if _ISO_DATE_RE.match(text):
        try:
            d = datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            raise DateParseError("DATE_UNPARSEABLE") from None
        return ParsedDate(d, "%Y-%m-%d", "day")

    if _YEAR_MONTH_RE.match(text):
        try:
            d = datetime.strptime(text + "-01", "%Y-%m-%d").date()
        except ValueError:
            raise DateParseError("DATE_UNPARSEABLE") from None
        return ParsedDate(d, "%Y-%m", "month")

    if _YEAR_RE.match(text):
        try:
            d = date(int(text), 1, 1)
        except ValueError:
            raise DateParseError("DATE_UNPARSEABLE") from None
        return ParsedDate(d, "%Y", "year")

    matches: list[tuple[str, date]] = []
    for fmt in _NUMERIC_FORMATS + _TEXT_FORMATS:
        try:
            matches.append((fmt, datetime.strptime(text, fmt).date()))
        except ValueError:
            continue
    if not matches:
        raise DateParseError("DATE_UNPARSEABLE")
    distinct = {d for _, d in matches}
    if len(distinct) > 1:
        # Genuinely two readings. A scope-level convention (if the caller
        # resolved one) picks between them; without one this stays an
        # explicit refusal rather than a silent guess.
        preferred = _DAY_FIRST_FORMATS if convention == "DMY" else _MONTH_FIRST_FORMATS if convention == "MDY" else None
        if preferred is None:
            raise DateParseError("DATE_AMBIGUOUS")
        narrowed = [(fmt, d) for fmt, d in matches if fmt in preferred]
        if len({d for _, d in narrowed}) != 1:
            # The convention does not actually disambiguate THIS value
            # (e.g. a day-first convention against a value only month-first
            # can parse): refuse rather than pick arbitrarily.
            raise DateParseError("DATE_AMBIGUOUS")
        matches = narrowed
    fmt, d = matches[0]
    return ParsedDate(d, fmt, "day")


def shift_date_text(parsed: ParsedDate, offset_days: int) -> str:
    shifted = parsed.value + timedelta(days=offset_days)
    if parsed.precision == "year":
        return f"{shifted.year:04d}"
    if parsed.precision == "month":
        return f"{shifted.year:04d}-{shifted.month:02d}"
    if parsed.precision == "datetime":
        return shifted.isoformat() + (parsed.time_suffix or "")
    # day precision: re-render in the same textual convention as the source
    return shifted.strftime(parsed.fmt)


def age_years_at(dob: date, at: date) -> int:
    years = at.year - dob.year - ((at.month, at.day) < (dob.month, dob.day))
    return max(0, years)
