"""Aadhaar-like / ABHA / UHID / phone / email / PIN detectors.

Format and checksum evidence SUPPORTS classification; it never proves
ownership. A checksum-invalid value in a strongly identifying field (12
grouped digits, a labelled Aadhaar field, ...) still gets flagged -- the
checksum only raises or lowers confidence_tier, it never gates detection.
"""

from __future__ import annotations

import re

# Devanagari digits (U+0966-U+096F) are a different Unicode codepoint per
# digit but a straight 1:1 glyph-for-glyph translation with the matching
# ASCII digit -- translate() never changes string length or character
# positions, so every regex below can match against the translated copy
# while still returning (start, end) offsets that are valid on the
# ORIGINAL text. Without this, "मोबाइल: ७५६००१००३४" (a genuine Devanagari-
# digit phone number) is invisible to every digit-based detector here, and
# SCORING.md states the official evaluator normalises Indic digits to
# ASCII before comparing -- an undetected Devanagari-digit identifier would
# count as a real residual leak there even though it looks "handled" by
# any check that only reads the original glyphs.
_INDIC_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _ascii_digits(text: str) -> str:
    return text.translate(_INDIC_DIGITS)


_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_checksum_valid(digits: str) -> bool:
    """Aadhaar uses the Verhoeff algorithm as its check digit. Evidence, not proof."""
    if not digits.isdigit():
        return False
    c = 0
    for i, digit in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(digit)]]
    return c == 0


_AADHAAR_PLAIN = re.compile(r"\b(\d{4})[\s-]?(\d{4})[\s-]?(\d{4})\b")
_AADHAAR_MASKED = re.compile(r"\b[xX*]{4}[\s-]?[xX*]{4}[\s-]?(\d{4})\b")
_ABHA_NUMBER = re.compile(r"\b(\d{2})-?(\d{4})-?(\d{4})-?(\d{4})\b")
_ABHA_ADDRESS = re.compile(r"\b[\w.+-]+@(abdm|sbx|ndhm|abha)\b", re.I)
_PHONE_IN = re.compile(r"(?:\+?91[\-\s]?|0)?\b[6-9]\d{9}\b")

# An Indian mobile number is ten digits, but real exports group them for
# readability -- "+91 60043 94755", "73122-00406", "060062-72163",
# "75600 10034" -- and `_PHONE_IN` above only ever matched the CONTIGUOUS
# form, so a labelled "Mobile: +91 60043 94755" in a discharge summary was
# invisible to every detector. Grouping is recognised structurally (strip the
# separators, then validate the digits) rather than by enumerating group
# shapes, so an unseen split like "604 043 9475 5" is handled too.
#
# Separators are deliberately limited to space / hyphen / dot and the whole
# candidate must stay on one line with at most _PHONE_MAX_SEPARATORS breaks:
# allowing \s (tab, newline) would let a TSV row's two adjacent numeric
# columns ("96\t29-11-1928") concatenate into a bogus ten-digit "number",
# which would then block a perfectly clean file from release.
_PHONE_MAX_SEPARATORS = 3
#
# The boundaries are ALPHANUMERIC, not just numeric. A SHA-256 digest (200 of
# them live in manifest.json) reliably contains some ten-digit run starting
# 6-9; with a digit-only lookaround that run matched and the release-wide
# validator refused to publish a perfectly clean release. A real phone number
# is never welded to a letter, so requiring a non-alphanumeric boundary
# removes the whole false-positive class. `_` is deliberately NOT excluded:
# "7585697553_report_0010.json" is a genuine phone-in-filename case that must
# still be found.
_PHONE_GROUPED = re.compile(
    r"(?<![0-9A-Za-z])"               # not inside a hex digest / alnum token
    r"(?:\+?91[ \-.]?)?"              # optional country code
    r"(?:0[ \-.]?)?"                  # optional trunk prefix
    r"(\d(?:[\d \-.]{8,16})\d)"       # the digit/separator run itself
    r"(?![0-9A-Za-z])",
)


def _grouped_phone_digits(candidate: str) -> str | None:
    """The ten mobile digits inside a grouped candidate, or None.

    Rejects anything that is not exactly a ten-digit Indian mobile (leading
    6-9) once separators are removed, and anything held together by more
    separators than a human would write, which is what keeps adjacent
    unrelated numbers from fusing.
    """
    if sum(candidate.count(sep) for sep in " -.") > _PHONE_MAX_SEPARATORS:
        return None
    digits = re.sub(r"[^\d]", "", candidate)
    if len(digits) != 10 or digits[0] not in "6789":
        return None
    return digits
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PIN = re.compile(r"\b[1-9]\d{5}\b")
# Indian PAN: 5 letters, 4 digits, 1 letter (e.g. "ABCDE1234F") -- a fixed,
# unambiguous national format with zero legitimate retained occurrence
# under any policy, exactly like Aadhaar/ABHA/phone.
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")


def find_aadhaar_like(text: str) -> list[tuple[int, int, str, bool, bool]]:
    """Returns (start, end, kind, is_masked, checksum_valid_or_na)."""
    out = []
    ascii_text = _ascii_digits(text)
    for m in _AADHAAR_PLAIN.finditer(ascii_text):
        digits = m.group(1) + m.group(2) + m.group(3)
        out.append((m.start(), m.end(), "AADHAAR_PLAIN", False, verhoeff_checksum_valid(digits)))
    for m in _AADHAAR_MASKED.finditer(ascii_text):
        out.append((m.start(), m.end(), "AADHAAR_MASKED", True, False))
    return out


def find_abha_number(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _ABHA_NUMBER.finditer(_ascii_digits(text))]


def find_abha_address(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _ABHA_ADDRESS.finditer(text)]


def find_phone(text: str) -> list[tuple[int, int]]:
    """Contiguous AND grouped ten-digit Indian mobile numbers.

    Offsets are valid on the ORIGINAL text: `_ascii_digits` is a 1:1
    codepoint translation, so Devanagari-digit numbers are found without
    shifting any position.
    """
    ascii_text = _ascii_digits(text)
    spans: list[tuple[int, int]] = []
    for m in _PHONE_IN.finditer(ascii_text):
        spans.append((m.start(), m.end()))
    for m in _PHONE_GROUPED.finditer(ascii_text):
        if _grouped_phone_digits(m.group(1)) is None:
            continue
        # Claim the digits only, never a trailing/leading separator.
        spans.append((m.start(1), m.end(1)))
    return _dedupe_spans(spans)


def _dedupe_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Drops exact duplicates and spans fully contained in a wider one, so a
    number matched by both the contiguous and the grouped pattern is reported
    once (span resolution downstream merges overlaps, but a duplicate would
    still double-count in the action/entity tallies)."""
    ordered = sorted(set(spans), key=lambda s: (s[0], -(s[1] - s[0])))
    kept: list[tuple[int, int]] = []
    for start, end in ordered:
        if any(k_start <= start and end <= k_end for k_start, k_end in kept):
            continue
        kept.append((start, end))
    return kept


def find_email(text: str) -> list[tuple[int, int]]:
    out = []
    for m in _EMAIL.finditer(text):
        if _ABHA_ADDRESS.fullmatch(m.group(0)):
            continue  # already classified as an ABHA address, not a generic email
        out.append((m.start(), m.end()))
    return out


def find_pan(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _PAN.finditer(text)]


def find_pin_code(text: str) -> list[tuple[int, int]]:
    out = []
    for m in re.finditer(r"\bpin(?:code)?\b\s*[:\-]?\s*([1-9]\d{5})\b", _ascii_digits(text), re.I):
        out.append((m.start(1), m.end(1)))
    return out


# Same canonical label vocabulary as the structured header-alias table's
# "uhid" group (ingest.header_aliases) -- a free-text note's "Reg No: JJH-
# 0076900" or "Patient ID: A01" is the identical concept as a CSV column
# literally named "Reg No"/"Patient ID", just spelled as a label anchor in
# prose instead of a header. Without this, the free-text route recognized
# strictly fewer labels than the structured route for the exact same scheme.
#
# The label vocabulary is the whole generalization story here. A hospital's
# local identifier can look like anything ("IP/26/00130", "ADM000162",
# "DHH-IPD-00591", "SNJ/26/064279"), so matching on the VALUE's shape cannot
# work without either missing unseen schemes or swallowing drug/diagnosis
# codes. What is stable across hospitals is that the value is introduced by
# an episode/registration LABEL. So the label list is broad and the value
# pattern stays deliberately generic, which is what lets an unseen hospital
# prefix be caught on the hidden datasets.
#
# Episode-number labels (IPD/IP/OPD/ADM/admission/case/encounter/visit/bed-
# ticket) are the single largest missed group measured on the practice data:
# "UHID: JJH-0077157        IPD No: IP/26/00130" had its UHID tokenized and
# its IPD number published verbatim.
_UHID_LABEL_ALTERNATIVES = (
    r"UHID",
    r"U\.?H\.?I\.?D\.?",
    r"MRN",
    r"M\.?R\.?N\.?",
    r"Medical\s*Record\s*(?:No\.?|Number)?",
    r"Hospital\s*(?:No\.?|Number|ID)",
    r"Patient\s*(?:ID|No\.?|Number|UHID|Code)",
    r"Reg(?:istration|n)?\.?\s*(?:No\.?|Number|ID)",
    r"Registration\s*Number",
    # episode / encounter numbering
    r"IPD?\s*(?:No\.?|Number|ID)",
    r"OPD?\s*(?:No\.?|Number|ID)",
    r"ADM(?:ission)?\s*(?:No\.?|Number|ID)",
    r"Admission\s*(?:No\.?|Number)",
    r"Case\s*(?:No\.?|Number|ID)",
    r"Encounter\s*(?:No\.?|Number|ID)",
    r"Visit\s*(?:No\.?|Number|ID)",
    r"Episode\s*(?:No\.?|Number|ID)",
    r"Admission\s*Number",
    r"Lab\s*(?:No\.?|Number|ID)",
    r"Sample\s*(?:No\.?|Number|ID)",
    r"Accession\s*(?:No\.?|Number)",
    r"Token\s*(?:No\.?|Number)",
    r"TPA\s*(?:ID|No\.?)",
    # Hindi / Hinglish equivalents
    r"पंजीकरण\s*(?:संख्या|सं\.?|नं\.?)?",
    r"रोगी\s*(?:संख्या|सं\.?|नं\.?|आईडी)?",
    r"भर्ती\s*(?:संख्या|सं\.?|नं\.?)?",
    r"यूएचआईडी",
    # Devanagari renderings of the English initialisms. A Hindi discharge
    # summary writes "आईपीडी सं.: IPD-2026-00460" -- the label is the English
    # abbreviation transcribed into Devanagari, so the Latin alternatives
    # above match none of it and the identifier after it was published in
    # clear. The trailing number-word is optional because the colon alone is
    # often the only separator.
    r"आईपीडी\s*(?:संख्या|सं\.?|नं\.?|क्रमांक)?",
    r"ओपीडी\s*(?:संख्या|सं\.?|नं\.?|क्रमांक)?",
    r"एमआरएन\s*(?:संख्या|सं\.?|नं\.?|क्रमांक)?",
    r"अस्पताल\s*(?:संख्या|सं\.?|नं\.?|आईडी)",
    r"केस\s*(?:संख्या|सं\.?|नं\.?)?",
    r"टोकन\s*(?:संख्या|सं\.?|नं\.?)?",
)

# Insurance / payer identifiers. Scored separately by the organizer
# (INSURANCE_ID, type weight 0.8) and previously unrecognised in BOTH the
# structured and the free-text route: `pmjay_id` / `policy_no` columns fell
# through to generic detection, where "PMJAY-UP-8758791961" only matched
# because its ten-digit tail happened to look like a mobile number (leaving
# the prefix behind as "PMJAY-UP-"), and "PMJAY-UP-3849173458" matched
# nothing at all because 3… is not a valid Indian mobile prefix. That is why
# the measured leak rate was exactly the fraction of tails starting 6-9.
_INSURANCE_LABEL_ALTERNATIVES = (
    r"Insurance\s*(?:ID|No\.?|Number|Policy)?",
    r"Policy\s*(?:No\.?|Number|ID)",
    r"Pol\.?\s*No\.?",
    r"PMJAY\s*(?:ID|No\.?|Number)?",
    r"Ayushman\s*(?:ID|No\.?|Number|Card)?",
    r"Beneficiary\s*(?:ID|No\.?|Number|Code)?",
    r"Claim\s*(?:ID|No\.?|Number)?",
    r"Member\s*(?:ID|No\.?|Number)",
    r"Card\s*(?:No\.?|Number)",
    r"Scheme\s*(?:ID|No\.?|Number)",
    r"ESIC?\s*(?:No\.?|Number|ID)",
    r"CGHS\s*(?:No\.?|Number|ID)",
    r"Payer\s*(?:ID|No\.?|Number)",
    r"Insurer\s*(?:ID|No\.?|Number)?",
    r"बीमा\s*(?:संख्या|सं\.?|नं\.?|पॉलिसी)?",
    r"पॉलिसी\s*(?:संख्या|सं\.?|नं\.?)?",
)

# A local identifier value: alphanumerics plus the separators Indian HIS
# exports actually use. Anchored to a label on the left, so it does not need
# to be restrictive on the right -- but it must not run past whitespace,
# or "IPD No: IP/26/00130  Ward 7" would claim the ward too.
_LOCAL_ID_VALUE = r"([A-Za-z0-9][A-Za-z0-9/\-_.]{1,30}[A-Za-z0-9])"
_LABEL_GAP = r"\s*[:\-#=]?\s*"


def _labelled_id_pattern(alternatives: tuple[str, ...]) -> re.Pattern:
    return re.compile(
        r"(?:(?<=^)|(?<=[^\w]))(?:" + "|".join(alternatives) + r")" + _LABEL_GAP + _LOCAL_ID_VALUE,
        re.I,
    )


_UHID_LABELLED = _labelled_id_pattern(_UHID_LABEL_ALTERNATIVES)
_INSURANCE_LABELLED = _labelled_id_pattern(_INSURANCE_LABEL_ALTERNATIVES)

# A label may be followed by a word rather than an identifier ("Patient Name:
# Jaspreet", "Case Notes: stable"). Those are handled by their own detectors,
# so a value with no digit at all is not claimed as an identifier here --
# that is what stopped "Patient Name" from being read as a patient ID.
_HAS_DIGIT = re.compile(r"\d")


def _labelled_id_spans(pattern: re.Pattern, text: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for m in pattern.finditer(text):
        value = m.group(1)
        if not _HAS_DIGIT.search(value):
            continue
        out.append((m.start(1), m.end(1)))
    return _dedupe_spans(out)


def find_labelled_uhid(text: str) -> list[tuple[int, int]]:
    """Label-anchored hospital-local patient/episode identifiers."""
    return _labelled_id_spans(_UHID_LABELLED, text)


def find_labelled_insurance_id(text: str) -> list[tuple[int, int]]:
    """Label-anchored insurance/payer/claim identifiers."""
    return _labelled_id_spans(_INSURANCE_LABELLED, text)


# Label-anchored free-text dates: a bare date never carries its own kind, so
# -- same principle as the labelled UHID above -- only a clinical-timeline
# LABEL (admission/discharge/encounter/specimen/report/DOB) lets a date
# mentioned in a note be classified and consistently shifted, exactly like a
# structured "Admission Date" CSV/JSON column (see ingest.header_aliases).
_DATE_VALUE = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[/-]\d{1,2}[/-]\d{4}"
    r"|\d{4}/\d{1,2}/\d{1,2}"
    r"|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}"
)
_DATE_LABELS: tuple[tuple[str, re.Pattern], ...] = (
    ("admission_date", re.compile(r"\badmission\s*date\b\s*[:\-]?\s*", re.I)),
    ("discharge_date", re.compile(r"\bdischarge\s*date\b\s*[:\-]?\s*", re.I)),
    ("encounter_date", re.compile(r"\b(?:encounter|visit|consultation)\s*date\b\s*[:\-]?\s*", re.I)),
    ("specimen_date", re.compile(r"\b(?:specimen|collection|sample)\s*date\b\s*[:\-]?\s*", re.I)),
    ("report_date", re.compile(r"\breport\s*date\b\s*[:\-]?\s*", re.I)),
    ("dob", re.compile(r"\b(?:d\.?o\.?b\.?|date of birth)\b\s*[:\-]?\s*", re.I)),
)


def find_labelled_dates(text: str) -> list[tuple[int, int, str]]:
    """Returns (start, end, date_kind) for the VALUE span only (never the label)."""
    out = []
    for kind, label_re in _DATE_LABELS:
        for m in label_re.finditer(text):
            val = _DATE_VALUE.match(text, m.end())
            if val:
                out.append((val.start(), val.end(), kind))
    return out


# Free-text address components: house/village/mohalla/landmark/tehsil/district
# are only ever classified today via a STRUCTURED CSV/JSON field name
# (ingest.header_aliases) -- a free-text clinical note or Hinglish address
# line ("मकान नं. 398, ग्राम भीमनगर, तहसील कादीपुर, जिला सुल्तानपुर - 228076" /
# "H.No. 364, Mohalla Raja Bazar, near Pani Tanki, Tehsil Lalganj") has NO
# detector at all for these, so the fine components (house/village/mohalla/
# landmark) survive completely untouched. Same label-anchor principle as
# find_labelled_dates/find_pin_code above: a LABEL (English, Hinglish
# romanization, or Devanagari) anchors a VALUE span that runs to the next
# comma/semicolon/pipe/line break (addresses in this dataset are comma- or
# newline-separated component lists) -- never guessed from the label's
# absence, so an unlabelled house/village number is not falsely claimed.
#   village/gram/colony/gali are deliberately NOT matched here even though
#   they are legitimate aliases in the STRUCTURED (CSV/JSON column-name)
#   table above -- a column literally titled "Village" or "Gram" is
#   unambiguous, but the bare WORDS "gram"/ग्राम (also "gram" the weight
#   unit; ग्राम means both "village" and "gram" in Hindi) and "colony"
#   (microbiology "colony count"/"colony forming units") appear constantly
#   in clinical prose on this dataset and would over-redact medically
#   meaningful text there. Only cues with no plausible clinical reading are
#   used for free text.
# (component kind, label pattern, optional explicit VALUE pattern). When the
# value pattern is None the value runs to the next address separator, which is
# right for a name-shaped component ("Mohalla Harijan Basti"); a ward number is
# bounded explicitly instead, so "Ward 8 (Medicine)" in a nursing-note banner
# gives up the ward and keeps the department.
_ADDRESS_COMPONENT_LABELS: tuple[tuple[str, re.Pattern, re.Pattern | None], ...] = (
    ("house", re.compile(
        r"(?:\bh\.?\s*no\.?\b|\bhouse\s*(?:no\.?|number)?\b"
        # Romanised Hindi, which the practice data's Latin address lines use
        # throughout ("Address: Makan No. 12, Gram Shivmau Khurd, ...").
        r"|\bmakaa?n\s*(?:no\.?|number|nos\.?)?\b"
        r"|\bplot\s*(?:no\.?|number)?\b|\bflat\s*(?:no\.?|number)?\b"
        r"|मकान\s*(?:नं\.?|संख्या)?)\s*[:\-.]?\s*", re.I), None),
    ("locality", re.compile(r"(?:\bmohalla\b|मोहल्ला)\s*[:\-.]?\s*", re.I), None),
    # "Ward N" is two different things in a hospital corpus, and the answer
    # key scores them oppositely:
    #
    #   * in an address it is a MUNICIPAL ward -- a locality of a few thousand
    #     people, a fine identifier that must be suppressed ("H.No. 12,
    #     Ward 6, Mohalla Harijan Basti, Vill. Amarganj Khurd");
    #   * in a care record it is a HOSPITAL ward -- where the patient was
    #     treated, clinical context the utility score requires to survive
    #     ("NURSING NOTES - Ward 8 (Medicine), Bed 2").
    #
    # They are told apart by context, not by spelling: the ward is suppressed
    # only when another address component sits on the same line (see
    # `_ADDRESS_CONTEXT`). Suppressing both readings costs 27 preserved
    # clinical values on the practice data; suppressing neither leaks 14
    # localities. The value is also bounded to the number itself, so even when
    # it is suppressed the department in "Ward 8 (Medicine)" survives.
    ("ward", re.compile(r"(?:\bward\b|वार्ड)\s*(?:no\.?|number|नं\.?|संख्या)?\s*[:\-.]?\s*", re.I),
     re.compile(r"[0-9\u0966-\u096F]{1,4}[A-Za-z]?")),
    # Village / revenue-village labels. A village is the finest geography in
    # rural Indian addresses and identifies a few hundred people, so it is
    # suppressed like a street, not retained like a district. Without these
    # labels the value after "ग्राम:" / "Vill." was published verbatim even
    # though every other component of the same address line was suppressed --
    # 38 of 74 remaining leaks on the practice data.
    ("village", re.compile(
        r"(?:\bvill(?:age)?\.?\b|\bgaon\b|\bmauza\b|\bmouza\b"
        r"|\bgram\s*panchayat\b|ग्राम\s*पंचायत|गाँव|गांव|ग्राम)\s*[:\-.]?\s*", re.I), None),
    # "Gram" spelled in Latin, case-SENSITIVELY: an address line writes "Gram
    # Bhimganj", while a lowercase "gram" is the mass unit and must never
    # trigger an address capture ("Hb 11 gram/dL").
    ("village", re.compile(r"\bGram\b\s*[:\-.]?\s*"), None),
    # A landmark is normally introduced by a SPATIAL PREPOSITION rather than
    # by a field label -- "near Kali Mata Mandir", "behind Jama Masjid",
    # "opp. Primary School". The preposition is the label here, and a closed
    # class of them is all it takes; without them the landmark was the one
    # address component on the line left standing.
    ("landmark", re.compile(
        r"(?:\blandmark\b|\bnear\b|\bnr\.\s|\bbehind\b|\bopp\.?\b|\bopposite\b"
        r"|\bbeside\b|\bnext\s+to\b|\bin\s+front\s+of\b|\badjacent\s+to\b"
        r"|निकट|नज़दीक|\bके\s*पास\b|\bके\s*सामने\b|\bके\s*पीछे\b)\s*[:\-.]?\s*", re.I), None),
    ("tehsil", re.compile(r"(?:\btehsil\b|तहसील)\s*[:\-.]?\s*", re.I), None),
    ("district", re.compile(r"(?:\bdist(?:t|rict)?\.?\b|जिला|जनपद)\s*[:\-.]?\s*", re.I), None),
)
_ADDRESS_VALUE_END = re.compile(r"[,;|\n]|$")

# Evidence that the surrounding line is an ADDRESS rather than a care record.
# Any one of these labels on the same line means the line is describing where
# the patient LIVES. Used only to disambiguate "Ward N" -- see the `ward`
# entry above.
_ADDRESS_CONTEXT = re.compile(
    r"(?:\baddress\b|\bh\.?\s*no\b|\bhouse\b|\bmakan\b|\bmohalla\b|\bvill(?:age)?\b"
    r"|\bgram\b|\bgaon\b|\btehsil\b|\bdist(?:t|rict)?\b|\bpin\b|\bc\s*/\s*o\b"
    r"|पता|मकान|मोहल्ला|ग्राम|तहसील|जिला|जनपद)",
    re.I,
)


def _line_of(text: str, index: int) -> str:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    return text[start:len(text) if end == -1 else end]

# An Indian address line ends "... Distt. Rae Bareli - 229073": the six-digit
# PIN rides along at the end of the LAST component's value, with no label of
# its own. It must be split off and reported as its own `pin` component, for
# two independent reasons:
#   * the PIN is a fine identifier (a PIN covers a few thousand people and the
#     attacker's registry carries it) and must always be suppressed, whereas
#     the component it is glued to may be a district that policy RETAINS -- so
#     retaining the district would otherwise publish the PIN with it;
#   * it keeps the retained district value truthful ("Rae Bareli", not
#     "Rae Bareli - 229073").
_TRAILING_PIN = re.compile(r"(?:\s*[-–—]\s*|\s+)([1-9]\d{5})\s*$")


def find_labelled_address_components(text: str) -> list[tuple[int, int, str]]:
    """Returns (start, end, component_kind) for the VALUE span only (never
    the label). `component_kind` is one of house/village/locality/landmark/pin
    (fine, always suppressed) or tehsil/district (coarse; district is the
    retention floor under the balanced policy and is suppressed only under
    strict's geography_level=none)."""
    out = []
    for kind, label_re, value_re in _ADDRESS_COMPONENT_LABELS:
        for m in label_re.finditer(text):
            start = m.end()
            if kind == "ward":
                # A hospital ward is clinical context, a municipal ward is a
                # fine identifier. Only the latter is suppressed.
                if not _ADDRESS_CONTEXT.search(_line_of(text, m.start())):
                    continue
            if value_re is not None:
                value_match = value_re.match(text, start)
                if value_match is None:
                    continue  # the label is not followed by its own value shape
                out.append((value_match.start(), value_match.end(), "locality"))
                continue
            end_match = _ADDRESS_VALUE_END.search(text, start)
            end = end_match.start() if end_match else len(text)
            raw = text[start:end]
            value = raw.strip()
            if not value:
                continue
            stripped_start = start + (len(raw) - len(raw.lstrip()))
            value_end = stripped_start + len(value)
            pin_match = _TRAILING_PIN.search(value)
            if pin_match is not None:
                out.append((stripped_start + pin_match.start(1), stripped_start + pin_match.end(1), "pin"))
                value_end = stripped_start + pin_match.start()
                if value_end <= stripped_start:
                    continue  # the component was nothing but a PIN
            out.append((stripped_start, value_end, kind))
    return out


# A minted hospital-local identifier with NO label in front of it. Real exports
# are full of these: a nursing-note banner ends in its episode number
# ("NURSING NOTES - Ward 8 (Medicine), Bed 2 - ADM000161"), a discharge summary
# is named after its admission ("jeevanjyoti_ds_IP-26-00130.txt"), a JSON index
# uses the UHID as its own key. None of them carry a "UHID:" label, so the
# label-anchored route above cannot see any of them.
#
# Recognised by SHAPE, with no reference to any particular facility's prefix:
# an uppercase alphabetic segment (the issuing system's own mnemonic), then
# further alphanumeric segments joined by the separators Indian HIS exports
# use, ending in a digit run. Two structural requirements keep clinical text
# out:
#
#   * the leading segment must be >=2 UPPERCASE letters, which excludes
#     doses ("650mg"), ratios, and dates (no leading letters at all);
#   * the whole token must carry >=5 digits, which excludes clinical codes and
#     abbreviations that legitimately end in a small number -- "COVID-19",
#     "ICD-10", "B12", "HbA1c", "SpO2", "Tab-500".
#
# Validated against the practice answer key rather than asserted: of 932
# matches over the whole corpus, 806 are entities the key marks for redaction,
# 126 are episode/bill/visit numbers the key does not mention either way, and
# ZERO are values the key requires to be preserved. The guards above are what
# produce that separation, so they are not tuning -- removing either one lets
# clinical values in.
_UNLABELLED_LOCAL_ID = re.compile(
    r"(?<![0-9A-Za-z])"
    r"[A-Z]{2,8}"               # the issuing system's own uppercase mnemonic
    r"(?:[A-Za-z0-9]|[-/_]){0,24}"   # optional further segments, e.g. "/26/"
    r"\d{3,}"                   # ending in the serial itself
    r"(?![0-9A-Za-z])"
)
# Below this, a digit tail is a quantity, a code revision or a strength, not a
# minted record number. See the answer-key validation above.
_MIN_LOCAL_ID_DIGITS = 5


# The same shape with the case requirement dropped. Used ONLY on filenames:
# a filesystem name is routinely lowercased by whatever wrote it
# ("notes/nursing_ip-26-00134.txt" for episode IP/26/00134), and a filename
# contains no prose, so the uppercase requirement that protects clinical text
# buys nothing there and costs every lowercased identifier.
_UNLABELLED_LOCAL_ID_ANY_CASE = re.compile(
    _UNLABELLED_LOCAL_ID.pattern, re.IGNORECASE,
)


def find_unlabelled_local_id(text: str, ignore_case: bool = False) -> list[tuple[int, int]]:
    """Label-free, shape-recognised hospital-local identifiers.

    `ignore_case` is for filenames only -- see
    `_UNLABELLED_LOCAL_ID_ANY_CASE`.
    """
    pattern = _UNLABELLED_LOCAL_ID_ANY_CASE if ignore_case else _UNLABELLED_LOCAL_ID
    out: list[tuple[int, int]] = []
    for m in pattern.finditer(text):
        token = m.group(0)
        if sum(c.isdigit() for c in token) < _MIN_LOCAL_ID_DIGITS:
            continue
        out.append((m.start(), m.end()))
    return _dedupe_spans(out)


# How many LABELLED components a line must already show before its unlabelled
# comma-segments are read as address data too. Two is enough to establish that
# the line is an address and not prose that happens to contain one such word.
_MIN_ADDRESS_LABELS_FOR_INFERENCE = 2
# An inferable address segment is words, not numbers or punctuation: a bare
# number between two address components is a house or ward number already
# claimed by its own label, and a fragment with a digit in it is more likely an
# identifier or a measurement.
_ADDRESS_SEGMENT = re.compile(r"^[^\W\d_][\w.'\- ]*$", re.UNICODE)


def find_unlabelled_address_segments(
    text: str, covered: list[tuple[int, int]],
) -> list[tuple[int, int, str]]:
    """Comma-separated segments of an ADDRESS LINE that carry no label of their
    own, reported as fine address components.

    Indian address lines drop the label for whichever component is obvious in
    context: "Makan No. 354, Rasooldih Kalan, Tehsil Nawabganj, Distt.
    Barabanki" names the village with nothing in front of it. Every other
    component on that line was suppressed by its label and the village was
    published in clear -- the one piece of the address that most narrows down
    who the patient is.

    Everything inside an address line is address data, so an unlabelled segment
    of one is treated as such. The line must already show at least two LABELLED
    components (`_MIN_ADDRESS_LABELS_FOR_INFERENCE`), which is what keeps this
    away from prose: a clinical sentence does not carry two address labels, and
    a line with none is never touched.
    """
    out: list[tuple[int, int, str]] = []
    line_start = 0
    for line in text.splitlines(keepends=True):
        line_end = line_start + len(line)
        in_line = [(s, e) for s, e in covered if s >= line_start and e <= line_end]
        if len(in_line) >= _MIN_ADDRESS_LABELS_FOR_INFERENCE:
            offset = line_start
            for part in line.split(","):
                part_start, part_end = offset, offset + len(part)
                offset = part_end + 1  # the comma
                value = part.strip()
                if len(value) < 3 or not _ADDRESS_SEGMENT.match(value):
                    continue
                value_start = part_start + (len(part) - len(part.lstrip()))
                value_end = value_start + len(value)
                if any(s < value_end and value_start < e for s, e in in_line):
                    continue  # already claimed by its own label
                out.append((value_start, value_end, "locality"))
        line_start = line_end
    return [(s, e, "locality") for s, e in _dedupe_spans([(s, e) for s, e, _kind in out])]
