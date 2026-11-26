"""Detection routes added in Gate 0.5, each with the leak it closed.

Every test below corresponds to a measured leak on the practice corpus, and
every one is written against the GENERAL rule rather than the specific value
that leaked -- the values here are deliberately not the practice data's
(different hospitals, different names, different prefixes), because the only
version of these rules worth having is the version that transfers to the
hidden datasets.

Each test also pins the thing the rule must NOT do, since every one of these
routes trades against clinical utility: a rule that redacts the leak and the
diagnosis with it is not a fix.
"""

import pytest

from nijsetu.detect import detect_in_text
from nijsetu.detect.identifiers import find_unlabelled_local_id
from nijsetu.detect.names import detect_free_text_names, find_organisation_spans
from nijsetu.ingest.header_aliases import is_field_label, resolve_header
from nijsetu.schema import FieldRole, SourceCoordinate

BASE = SourceCoordinate(file_no=0, file_path="x.txt", record_no=0, path="body")


def _roles(text: str, **kw) -> dict[str, FieldRole]:
    return {f.original_text: f.role for f in detect_in_text(text, BASE, **kw)}


def _texts(text: str, role: FieldRole, **kw) -> set[str]:
    return {f.original_text for f in detect_in_text(text, BASE, **kw) if f.role == role}


# --- label-free minted identifiers ------------------------------------------

@pytest.mark.parametrize("value", [
    "KGH-0012345",          # prefix + long digit run
    "IPD-2027-00123",       # prefix + year + serial
    "ADM000912",            # prefix glued to the digits
    "RH/27/004512",         # slash-separated
    "CMC-IPD-00741",        # two alphabetic segments
    "BLK2027-114099",       # digits inside the prefix segment
])
def test_unlabelled_minted_identifier_is_recognised(value):
    """A hospital banner or a key ends in its episode number with no label in
    front of it ("NURSING NOTES - Ward 3 (Surgery), Bed 7 - ADM000912")."""
    assert find_unlabelled_local_id(f"NURSING NOTES - Bed 7 - {value}")


@pytest.mark.parametrize("value", [
    "COVID-19",     # a disease code, not a record number
    "ICD-10",
    "B12",          # a vitamin
    "HbA1c",
    "SpO2",
    "Tab-500",      # a strength
    "E11.9",        # an ICD-10 code
    "12-10-2026",   # a date: no alphabetic segment at all
    "134/84",       # a blood pressure
])
def test_clinical_codes_are_not_mistaken_for_identifiers(value):
    assert not find_unlabelled_local_id(value), value


def test_identifier_shape_requires_both_guards():
    """Each guard is load-bearing: >=2 uppercase letters excludes doses and
    dates, >=5 digits excludes code revisions."""
    assert not find_unlabelled_local_id("AB-123")     # only 3 digits
    assert not find_unlabelled_local_id("a-123456")   # only 1 leading letter
    assert find_unlabelled_local_id("AB-123456")      # both satisfied


def test_lowercase_identifiers_are_recognised_only_for_filenames():
    """A filesystem name is routinely lowercased and holds no prose, so case
    cannot be required there; prose keeps the uppercase guard."""
    assert not find_unlabelled_local_id("nursing_ip-27-00134")
    assert find_unlabelled_local_id("nursing_ip-27-00134", ignore_case=True)


# --- qualified headers -------------------------------------------------------

@pytest.mark.parametrize(("header", "canonical"), [
    ("hospital_uhid", "uhid"),
    ("patient_dob", "dob"),
    ("primary_contact_number", "phone"),
    ("patient_village", "village"),
    ("registered_patient_mobile", "phone"),
])
def test_qualified_header_resolves_to_its_head_noun(header, canonical):
    """A qualifier bolted onto a known field must not make the field unknown:
    an unresolved header keeps FieldRole.OTHER, and OTHER means retain."""
    assert resolve_header(header)[0] == canonical


@pytest.mark.parametrize("header", ["test name", "lab id", "bill number", "result value"])
def test_generic_head_noun_alone_decides_nothing(header):
    """"Name"/"ID"/"Number" head both patient and non-patient fields. Guessing
    would resolve "test name" to a patient name and destroy every lab test."""
    assert resolve_header(header) == (None, FieldRole.OTHER)


# --- field labels are not values --------------------------------------------

@pytest.mark.parametrize("label", ["नाम", "आयु", "ग्राम", "Naam", "Umar", "Gaon", "DOB"])
def test_known_field_labels_are_recognised_as_labels(label):
    assert is_field_label(label)


def test_hindi_labels_are_not_claimed_as_patient_names():
    """The Hindi labels transliterate onto real-name consonant skeletons, so
    the cross-script gazetteer claimed them -- and every document carries the
    same labels, which bridged unrelated patients into one cluster."""
    text = "नाम: सुरेश वर्मा   आयु: 54 वर्ष\nग्राम: कुसुमपुर\nसुरेश वर्मा को 3 दिन से बुखार है।"
    names = {s.text for s in detect_free_text_names(text) if s.role == FieldRole.PATIENT_NAME}
    assert not ({"नाम", "आयु", "वर्ष", "ग्राम", "दिन"} & names)


def test_the_patient_s_own_name_in_the_sentence_body_is_still_claimed():
    """The guard above must not take the name with it. A date ending the
    previous line once made the next line's first word look like a measure
    word, which suppressed exactly this."""
    text = "तारीख: 11-10-2026\nसुरेश वर्मा को 3 दिन से बुखार है।"
    names = {s.text for s in detect_free_text_names(text) if s.role == FieldRole.PATIENT_NAME}
    assert any("सुरेश" in n or "वर्मा" in n for n in names), names


# --- institution names are not people ---------------------------------------

@pytest.mark.parametrize("banner", [
    "Kusum Deep Charitable Hospital",
    "Navjeevan Seva Sadan",
    "Suraj Diagnostics",
    "Matri Chhaya Nursing Home",
    "Pragati Pathology Laboratory",
])
def test_institution_banners_are_recognised_structurally(banner):
    """Recognised by the generic TYPE noun at the end, never by the facility's
    own brand words -- those are real given names, so excluding them by
    spelling would miss patients called that and would not transfer to a
    hospital this repository has never seen."""
    assert find_organisation_spans(f"{banner} - OPD Slip")


def test_a_given_name_inside_a_hospital_name_is_not_a_patient():
    text = "Kusum Deep Charitable Hospital\nPatient: Suresh Verma\nTab Paracetamol 650mg"
    names = _texts(text, FieldRole.PATIENT_NAME)
    assert not any(n in {"Kusum", "Deep"} for n in names), names


def test_the_same_given_name_IS_a_patient_when_it_is_the_patient():
    """The structural rule excludes the institution's SPAN, never the word --
    a patient actually called Kusum is still found."""
    names = _texts("Patient: Kusum Verma\nTab Paracetamol 650mg", FieldRole.PATIENT_NAME)
    assert any("Kusum" in n for n in names), names


# --- address components ------------------------------------------------------

@pytest.mark.parametrize(("line", "leaked"), [
    ("Address: Makan No. 12, Gram Kusumpur, Tehsil Hasanganj, Distt. Unnao - 209609", "Kusumpur"),
    ("Address: H.No. 7, Vill. Chanddih, Tehsil Lambhua, Distt. Sultanpur", "Chanddih"),
    ("पता: मकान नं. 9, ग्राम कुसुमपुर, तहसील हसनगंज, जिला उन्नाव", "कुसुमपुर"),
])
def test_village_labels_are_recognised_in_both_scripts(line, leaked):
    assert leaked in " ".join(_texts(line, FieldRole.FINE_ADDRESS)), line


@pytest.mark.parametrize("landmark", [
    "near Panchayat Bhawan", "behind Jama Masjid", "opp. Primary School",
    "opposite Bus Stand", "beside Shiv Mandir",
])
def test_landmarks_introduced_by_a_spatial_preposition(landmark):
    """A landmark is introduced by a preposition, not by a field label."""
    line = f"Address: H.No. 7, {landmark}, Vill. Chanddih, Distt. Unnao"
    fine = " ".join(_texts(line, FieldRole.FINE_ADDRESS))
    assert landmark.split(maxsplit=1)[1].split(",")[0] in fine, (landmark, fine)


def test_unlabelled_segment_of_an_address_line_is_address_data():
    """Indian address lines drop the label for whichever component is obvious:
    the village here has nothing in front of it, and every other component on
    the line is suppressed by its own label."""
    line = "Address: Makan No. 354, Rasoolpur Kalan, Tehsil Hasanganj, Distt. Unnao"
    assert "Rasoolpur Kalan" in " ".join(_texts(line, FieldRole.FINE_ADDRESS))


def test_a_clinical_line_is_never_treated_as_an_address():
    """The inference above requires two labelled address components on the
    line, which is what keeps it away from clinical prose."""
    line = "Adv: Low salt diet, daily walk, review after 2 wks, continue Metformin"
    assert not _texts(line, FieldRole.FINE_ADDRESS), line


def test_municipal_ward_is_suppressed_but_hospital_ward_is_kept():
    """"Ward 8" is a municipal locality in an address and the place a patient
    was treated in a care record. The answer key scores them oppositely, and
    they are told apart by context, not spelling."""
    address = "Address: H.No. 7, Ward 6, Mohalla Shanti Nagar, Vill. Chanddih, Distt. Unnao"
    assert any(f.original_text == "6" for f in detect_in_text(address, BASE))
    banner = "NURSING NOTES - Ward 8 (Medicine), Bed 2 - ADM000912"
    assert not any(f.original_text == "8" for f in detect_in_text(banner, BASE))


# --- names with no label, no cue, no gazetteer entry ------------------------

@pytest.mark.parametrize("term", ["behenji", "bhai", "babu", "didi", "mama", "bhaiya"])
def test_kinship_address_terms_name_the_person_before_them(term):
    """Hinglish referrals address the patient by a kinship term."""
    assert _texts(f"Kusum {term} ko 3 din se bukhar hai", FieldRole.PATIENT_NAME)


@pytest.mark.parametrize("relation", ["attendant", "guardian", "son", "wife", "next of kin"])
def test_english_possessive_names_the_person_before_the_apostrophe(relation):
    """A lab remark names the patient only as the possessor of a relative."""
    text = f"Sample received from Kusum's {relation} (S/o Rahul Tiwari)."
    assert "Kusum" in " ".join(_texts(text, FieldRole.PATIENT_NAME)), text


def test_a_corpus_declared_name_is_found_in_free_text_without_a_gazetteer_entry():
    """The bundled gazetteer cannot hold every Indian given name. A structured
    column in the same corpus declares the name, so the corpus supplies the
    list the fixed gazetteer lacks -- which is why this transfers to datasets
    whose names this repository has never seen."""
    note = "Jhilmil ka BP 120/78, BP abhi bhi high hai."
    assert not _texts(note, FieldRole.PATIENT_NAME)
    found = _texts(note, FieldRole.PATIENT_NAME, corpus_name_tokens=frozenset({"jhilmil"}))
    assert "Jhilmil" in found, found


def test_a_corpus_name_token_never_outranks_an_explicit_provider_cue():
    """Corpus-derived tokens are the WEAKEST tier on purpose: a patient called
    Ramesh elsewhere must not turn every attending Dr. Ramesh into a patient
    and destroy the physician the utility score expects to survive."""
    roles = _roles("Dr. Ramesh Srivastava took evening round.",
                   corpus_name_tokens=frozenset({"ramesh", "srivastava"}))
    assert FieldRole.PROVIDER_NAME in roles.values()
    assert FieldRole.PATIENT_NAME not in roles.values(), roles
