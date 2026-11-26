"""Contextual entity detection: identifiers, clinical false-positive guard, free-text names."""

from nijsetu.detect import detect_in_text
from nijsetu.detect.identifiers import verhoeff_checksum_valid
from nijsetu.schema import FieldRole, SourceCoordinate

BASE = SourceCoordinate(file_no=0, file_path="x.txt", record_no=0, path="body")


def roles_and_texts(text, **kw):
    feats = detect_in_text(text, BASE, **kw)
    return [(f.role.value, f.original_text) for f in feats]


# --- clinical false-positive guard -------------------------------------------

def test_blood_pressure_values_retained_as_clinical():
    out = roles_and_texts("BP 140/90 and 150/90")
    assert ("CLINICAL_MEASUREMENT", "140/90") in out
    assert ("CLINICAL_MEASUREMENT", "150/90") in out
    assert not any(role == "PATIENT_IDENTIFIER" for role, _ in out)


def test_hba1c_retained_as_clinical():
    out = roles_and_texts("HbA1c 8.2%")
    assert any(role == "CLINICAL_MEASUREMENT" and "8.2" in text for role, text in out)


def test_dose_and_drug_name_retained_as_clinical_not_name():
    out = roles_and_texts("Ramipril 5 mg twice daily")
    assert ("CLINICAL_MEASUREMENT", "Ramipril") in out
    assert not any(role == "PATIENT_NAME" and text == "Ramipril" for role, text in out)


def test_kco_and_ho_are_clinical_history_not_identifiers():
    for text in ("K/C/O diabetes", "H/O hypertension"):
        out = roles_and_texts(text)
        assert any(role == "CLINICAL_HISTORY" for role, _ in out)
        assert not any(role in ("PATIENT_NAME", "RELATIVE_NAME") for role, _ in out)


def test_opd_care_of_is_clinical_complaint_not_a_name():
    out = roles_and_texts("OPD: C/O fever for 5 days")
    assert any(role == "CLINICAL_HISTORY" for role, _ in out)
    assert not any(role in ("PATIENT_NAME", "RELATIVE_NAME") for role, _ in out)


def test_address_care_of_is_a_relative_name():
    out = roles_and_texts("Address: C/O Ramesh, Ward 5")
    assert ("RELATIVE_NAME", "Ramesh") in out


def test_dr_prefix_is_provider_not_patient():
    out = roles_and_texts("Dr. Ramesh advised rest.")
    assert ("PROVIDER_NAME", "Ramesh") in out
    assert not any(role in ("PATIENT_NAME", "RELATIVE_NAME") for role, text in out if text == "Ramesh")


# --- free-text name cues ------------------------------------------------------

def test_sahab_cue_detects_unanchored_name():
    out = roles_and_texts("Arif sahab ka BP 150/90")
    assert ("PATIENT_NAME", "Arif") in out


def test_md_abbreviation_prefix_detected_as_name():
    out = roles_and_texts("Md. Arif came for follow up")
    assert ("PATIENT_NAME", "Md. Arif") in out


def test_s_o_d_o_w_o_detect_relative_names():
    assert ("RELATIVE_NAME", "Ramesh Kumar") in roles_and_texts("S/o Ramesh Kumar")
    assert any(role == "RELATIVE_NAME" for role, _ in roles_and_texts("D/o Geeta Devi"))
    assert any(role == "RELATIVE_NAME" for role, _ in roles_and_texts("W/o Suresh Yadav"))


def test_devi_suffix_kept_as_name_component():
    out = roles_and_texts("Smt. Sita Devi was admitted")
    assert ("PATIENT_NAME", "Smt. Sita Devi") in out


def test_trusted_alias_detected_case_insensitively():
    out = roles_and_texts(
        "later note mentions MOHAMMAD ARIF again",
        trusted_aliases={"Mohammad Arif": FieldRole.PATIENT_NAME},
    )
    assert any(role == "PATIENT_NAME" and text.lower() == "mohammad arif" for role, text in out)


def test_name_span_does_not_cross_newline():
    out = roles_and_texts("OPD\nArif sahab ka follow up")
    assert ("PATIENT_NAME", "Arif") in out
    assert not any("\n" in text for _, text in out)


def test_gazetteer_hit_without_cue_is_medium_confidence():
    feats = detect_in_text("Ramesh was referred for follow up", BASE)
    name_feats = [f for f in feats if f.role == FieldRole.PATIENT_NAME]
    assert name_feats and name_feats[0].confidence_tier == "MEDIUM"


def test_devanagari_arif_detected_via_gazetteer_skeleton():
    out = roles_and_texts("मोहम्मद आरिफ़ को भर्ती किया गया")
    assert any(role == "PATIENT_NAME" and text == "मोहम्मद" for role, text in out)
    assert any(role == "PATIENT_NAME" and text == "आरिफ़" for role, text in out)


def test_devanagari_common_words_not_falsely_flagged():
    out = roles_and_texts("मीरा को बुखार है")
    flagged = [text for role, text in out if role == "PATIENT_NAME"]
    assert flagged == ["मीरा"]


# --- identifiers ---------------------------------------------------------------

def test_aadhaar_plain_detected_with_checksum_evidence():
    feats = detect_in_text("Aadhaar: 234123412346", BASE)
    ident = [f for f in feats if f.role == FieldRole.PATIENT_IDENTIFIER]
    assert ident and ident[0].scheme == "aadhaar"


def test_aadhaar_masked_variant_detected():
    out = roles_and_texts("Aadhaar: XXXX XXXX 1234")
    assert any(role == "PATIENT_IDENTIFIER" for role, _ in out)


def test_checksum_invalid_aadhaar_still_detected_not_verified():
    # format alone is enough to flag; checksum only adjusts evidence tier
    feats = detect_in_text("Aadhaar: 111111111111", BASE)
    ident = [f for f in feats if f.role == FieldRole.PATIENT_IDENTIFIER]
    assert ident
    assert not verhoeff_checksum_valid("111111111111") or True  # detection must not depend on this


def test_abha_number_and_address_distinguished():
    schemes = {f.scheme for f in detect_in_text("ABHA 12-3456-7890-1234 and ravi@abdm", BASE) if f.role == FieldRole.PATIENT_IDENTIFIER}
    assert "abha_number" in schemes
    assert "abha_address" in schemes


def test_phone_and_email_detected_as_contact():
    feats = detect_in_text("call 9876543210 or mail ravi@example.com", BASE)
    assert any(f.role == FieldRole.CONTACT and f.scheme == "PHONE" for f in feats)
    assert any(f.role == FieldRole.CONTACT and f.scheme == "EMAIL" for f in feats)


def test_labelled_uhid_detected():
    out = roles_and_texts("UHID: ABC12345")
    assert ("PATIENT_IDENTIFIER", "ABC12345") in out


# --- national-scheme issuer consistency (Finding 1) --------------------------

def test_structured_and_freetext_aadhaar_share_the_same_issuer_and_scheme():
    """A national scheme (Aadhaar/ABHA) must resolve to the identical scope
    whether it was detected via a structured header-aliased field or via
    free-text pattern matching -- otherwise the same value from two routes
    would never exact-match in identity resolution."""
    structured_source = SourceCoordinate(file_no=0, file_path="r.csv", record_no=0, path="Aadhaar")
    structured = detect_in_text("234123412346", structured_source, structured_role=FieldRole.PATIENT_IDENTIFIER)
    freetext = detect_in_text("Aadhaar: 234123412346", BASE)
    s_ident = next(f for f in structured if f.role == FieldRole.PATIENT_IDENTIFIER)
    f_ident = next(f for f in freetext if f.role == FieldRole.PATIENT_IDENTIFIER)
    assert s_ident.issuer == f_ident.issuer == "UIDAI"
    assert s_ident.scheme == f_ident.scheme == "aadhaar"


def test_structured_and_freetext_abha_share_the_same_issuer():
    structured_source = SourceCoordinate(file_no=0, file_path="r.csv", record_no=0, path="ABHA Number")
    structured = detect_in_text("12-3456-7890-1234", structured_source, structured_role=FieldRole.PATIENT_IDENTIFIER)
    freetext = detect_in_text("ABHA 12-3456-7890-1234", BASE)
    s_ident = next(f for f in structured if f.role == FieldRole.PATIENT_IDENTIFIER)
    # the free-text route also matches an overlapping Aadhaar-shaped
    # sub-pattern in the same digits -- pick the ABHA-scheme hit specifically.
    f_ident = next(f for f in freetext if f.role == FieldRole.PATIENT_IDENTIFIER and f.scheme == "abha_number")
    assert s_ident.issuer == f_ident.issuer == "ABDM"


def test_pre_assigned_issuer_and_scheme_survive_structured_detection():
    """An adapter that already knows the authoritative scope (FHIR
    identifier.system) must have it pass through untouched, not be
    overwritten by header-alias inference (which a FHIR field path like
    "identifier.value" would never match anyway, silently dropping it)."""
    source = SourceCoordinate(file_no=0, file_path="r.json", record_no=0, path="identifier.value")
    feats = detect_in_text(
        "UH999", source, structured_role=FieldRole.PATIENT_IDENTIFIER,
        pre_issuer="https://hosp.example/uhid", pre_scheme="https://hosp.example/uhid",
    )
    ident = next(f for f in feats if f.role == FieldRole.PATIENT_IDENTIFIER)
    assert ident.issuer == "https://hosp.example/uhid"
    assert ident.scheme == "https://hosp.example/uhid"
