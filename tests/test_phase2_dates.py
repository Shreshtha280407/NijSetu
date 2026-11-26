"""Unit tests: flexible date parsing + format-preserving shifting (phase2.dates)."""

from __future__ import annotations

from datetime import date

import pytest

from nijsetu.phase2.dates import DateParseError, age_years_at, parse_flexible_date, shift_date_text


def test_parses_iso_date():
    p = parse_flexible_date("2024-10-12")
    assert p.value == date(2024, 10, 12)
    assert p.precision == "day"


def test_parses_iso_datetime_preserves_time_suffix():
    p = parse_flexible_date("2024-10-12T08:30:00+05:30")
    assert p.value == date(2024, 10, 12)
    assert p.precision == "datetime"
    assert p.time_suffix == "T08:30:00+05:30"


def test_rejects_ambiguous_day_month_order():
    # day<=12: valid as both dd/mm/yyyy (5 June) and US mm/dd/yyyy (6 May).
    with pytest.raises(DateParseError) as exc:
        parse_flexible_date("05/06/2024")
    assert exc.value.reason_code == "DATE_AMBIGUOUS"


def test_unambiguous_numeric_date_parses():
    p = parse_flexible_date("25/12/2024")  # only valid as dd/mm/yyyy
    assert p.value == date(2024, 12, 25)


def test_never_invents_missing_year():
    with pytest.raises(DateParseError) as exc:
        parse_flexible_date("18 November")
    assert exc.value.reason_code == "DATE_UNPARSEABLE"


def test_rejects_empty_text():
    with pytest.raises(DateParseError):
        parse_flexible_date("")


def test_shift_preserves_iso_format():
    p = parse_flexible_date("2024-10-12")
    assert shift_date_text(p, 5) == "2024-10-17"


def test_shift_preserves_original_non_iso_format():
    p = parse_flexible_date("25/12/2024")
    assert shift_date_text(p, 1) == "26/12/2024"


def test_shift_handles_month_rollover_and_leap_year():
    p = parse_flexible_date("2024-02-28")  # 2024 is a leap year
    assert shift_date_text(p, 1) == "2024-02-29"
    assert shift_date_text(p, 2) == "2024-03-01"


def test_shift_preserves_datetime_time_and_timezone_suffix():
    p = parse_flexible_date("2024-10-12T08:30:00+05:30")
    assert shift_date_text(p, 3) == "2024-10-15T08:30:00+05:30"


def test_required_worked_case_five_day_stay_preserved_after_shift():
    admission = parse_flexible_date("2024-10-12")
    discharge = parse_flexible_date("2024-10-17")
    offset = 15
    shifted_admission = shift_date_text(admission, offset)
    shifted_discharge = shift_date_text(discharge, offset)
    assert shifted_admission == "2024-10-27"
    assert shifted_discharge == "2024-11-01"
    gap = parse_flexible_date(shifted_discharge).value - parse_flexible_date(shifted_admission).value
    assert gap.days == 5


def test_age_years_at_handles_birthday_not_yet_reached():
    dob = date(1990, 11, 30)
    assert age_years_at(dob, date(2024, 11, 20)) == 33
    assert age_years_at(dob, date(2024, 12, 1)) == 34
