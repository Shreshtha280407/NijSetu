"""Canonical field roles and their known registration/billing/clinical header aliases."""

from __future__ import annotations

import re

from ..schema import FieldRole

# Splits a camelCase/PascalCase boundary (lowercase/digit followed by an
# uppercase letter) so "patientName"/"PatientName" normalize the same way
# as "patient_name"/"Patient Name" -- a JSON key that never matches any
# alias falls through to free-text detection, which can silently miss an
# unfamiliar (non-gazetteer, no-cue) name entirely (see tests/
# test_ingest_adapters.py's camelCase regression cases).
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# canonical name -> (role, {lowercased alias strings, whitespace/punctuation-insensitive})
HEADER_ALIASES: dict[str, tuple[FieldRole, frozenset[str]]] = {
    "patient_name": (FieldRole.PATIENT_NAME, frozenset({
        "patient name", "name", "patient", "pt name", "pt", "naam", "patient full name",
    })),
    "relative_name": (FieldRole.RELATIVE_NAME, frozenset({
        "guardian name", "relative name", "next of kin", "nominee name", "caretaker name",
        "s/o", "d/o", "w/o", "guardian", "father name", "mother name", "spouse name",
    })),
    "provider_name": (FieldRole.PROVIDER_NAME, frozenset({
        "doctor", "doctor name", "consultant", "physician", "attending physician",
        "treating doctor", "dr", "provider name",
    })),
    "facility_name": (FieldRole.FACILITY_NAME, frozenset({
        "hospital", "hospital name", "facility", "facility name", "clinic", "clinic name",
    })),
    "uhid": (FieldRole.PATIENT_IDENTIFIER, frozenset({
        "uhid", "mrn", "hospital no", "hospital number", "patient id", "registration no",
        "registration number", "reg no", "patient uhid",
    })),
    "aadhaar": (FieldRole.PATIENT_IDENTIFIER, frozenset({
        "aadhaar", "aadhar", "aadhaar no", "aadhar no", "aadhaar number", "uid", "uidai",
    })),
    "abha_number": (FieldRole.PATIENT_IDENTIFIER, frozenset({
        "abha number", "abha no", "abha id",
    })),
    "abha_address": (FieldRole.PATIENT_IDENTIFIER, frozenset({
        "abha address", "abha addr",
    })),
    "billing_id": (FieldRole.PATIENT_IDENTIFIER, frozenset({
        "bill no", "invoice no", "claim id", "beneficiary id", "billing id", "account id",
    })),
    "phone": (FieldRole.CONTACT, frozenset({
        "phone", "mobile", "contact", "contact no", "contact number", "mobile number",
        "phone number", "telephone",
    })),
    "email": (FieldRole.CONTACT, frozenset({"email", "email id", "e-mail", "mail id"})),
    "dob": (FieldRole.DATE, frozenset({
        "dob", "date of birth", "birth date", "d.o.b", "d.o.b.",
    })),
    "admission_date": (FieldRole.DATE, frozenset({
        "admission date", "date of admission", "admitted on", "admit date",
    })),
    "discharge_date": (FieldRole.DATE, frozenset({
        "discharge date", "date of discharge", "discharged on",
    })),
    "encounter_date": (FieldRole.DATE, frozenset({
        "encounter date", "visit date", "consultation date", "date of visit",
    })),
    "specimen_date": (FieldRole.DATE, frozenset({
        "specimen date", "collection date", "sample date", "date of collection",
    })),
    "report_date": (FieldRole.DATE, frozenset({
        "report date", "date of report", "result date", "reported on",
    })),
    "address": (FieldRole.FINE_ADDRESS, frozenset({
        "address", "residential address", "full address", "home address",
    })),
    "ward": (FieldRole.FINE_ADDRESS, frozenset({"ward", "ward no", "ward number"})),
    "district": (FieldRole.COARSE_GEOGRAPHY, frozenset({"district", "tehsil", "block"})),
    "pin": (FieldRole.FINE_ADDRESS, frozenset({"pin", "pincode", "pin code", "postal code"})),
}


def normalize_header(raw: str) -> str:
    spaced = _CAMEL_BOUNDARY.sub(" ", raw)
    return " ".join(spaced.strip().lower().replace("_", " ").split())


def resolve_header(raw: str) -> tuple[str | None, FieldRole]:
    """Returns (canonical_name_or_None, role). Unknown headers keep OTHER role."""
    norm = normalize_header(raw)
    for canonical, (role, aliases) in HEADER_ALIASES.items():
        if norm in aliases:
            return canonical, role
    return None, FieldRole.OTHER
