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
        # Legacy/US-style exports split the name into two columns, and the
        # cp1252 legacy registration export in the practice data does exactly
        # this ("LastName", "FirstName"). Without these the whole name column
        # fell through to OTHER and was published verbatim.
        "full name", "pt full name", "नाम", "रोगी का नाम", "मरीज का नाम",
        # Romanised Hindi. A real export in the practice data uses an entirely
        # Hinglish header row ("Reg No / Naam / Ling / Umar / Janm Tithi /
        # Pita-Pati ka Naam / Pata / Gaon / Tehsil / Jila / Pin Code"), so the
        # romanised spellings are first-class aliases, not an afterthought.
        "naam", "poora naam", "marij ka naam", "rogi ka naam", "naam marij",
    })),
    # A split name. Separate canonicals so a legacy export carrying BOTH
    # "FirstName" and "LastName" (the cp1252 registration export does) does
    # not read as two columns fighting over one field. Same role, so both
    # halves get the identical privacy action.
    "name_given": (FieldRole.PATIENT_NAME, frozenset({
        "first name", "firstname", "given name", "fname", "middle name",
    })),
    "name_family": (FieldRole.PATIENT_NAME, frozenset({
        "last name", "lastname", "surname", "family name", "lname",
    })),
    "relative_name": (FieldRole.RELATIVE_NAME, frozenset({
        "guardian name", "relative name", "next of kin", "nominee name", "caretaker name",
        "s/o", "d/o", "w/o", "guardian", "father name", "mother name", "spouse name",
        "attendant", "attendant name", "informant", "informant name", "c/o",
        "care of", "husband name", "wife name", "parent name", "kin name",
        "पिता का नाम", "अभिभावक", "परिजन", "पति का नाम", "माता का नाम",
        # Romanised Hindi, including the compound "father-or-husband" column
        # Indian registration forms habitually use.
        "pita ka naam", "pati ka naam", "pita pati ka naam", "pita/pati ka naam",
        "mata ka naam", "abhibhavak", "sambandhi", "sambandhi ka naam",
        "pita", "pati", "mata",
    })),
    "provider_name": (FieldRole.PROVIDER_NAME, frozenset({
        "doctor", "doctor name", "consultant", "physician", "attending physician",
        "treating doctor", "dr", "provider name",
    })),
    "facility_name": (FieldRole.FACILITY_NAME, frozenset({
        "hospital", "hospital name", "facility", "facility name", "clinic", "clinic name",
    })),
    # Patient-level hospital key. Deliberately SEPARATE canonicals for the
    # episode- and specimen-level keys below: a lab CSV legitimately carries
    # both a `sample_id` and a `uhid` column, and folding them onto one
    # canonical makes two columns claim the same field, which the adapter
    # (correctly) reports as an ambiguous mapping. Distinct canonicals also
    # mean distinct tokenization namespaces, so a specimen number and a
    # patient number can never collide onto the same public token.
    "uhid": (FieldRole.PATIENT_IDENTIFIER, frozenset({
        "uhid", "mrn", "hospital no", "hospital number", "patient id", "registration no",
        "registration number", "reg no", "patient uhid",
        "panjikaran no", "panjikaran sankhya", "rogi sankhya", "marij id",
        "patientid", "patient no", "patient number", "patient code", "pid",
        "medical record no", "medical record number", "m.r.n", "m.r.n.",
        "hosp no", "hospital id", "local id", "patient key",
        "पंजीकरण संख्या", "रोगी संख्या", "यूएचआईडी",
        # A REFERENCE to the patient from a child record. This is how an OPD
        # visit, a lab result or a claim names its patient when it carries no
        # name of its own ("patient_ref": "UHID-010565"). Unrecognised, such a
        # record had neither a name nor a scoped identifier, so no blocking key
        # could ever make it a link candidate -- the single largest source of
        # missed candidate pairs measured on the practice data. Deliberately
        # NOT the bare words "ref"/"subject", which are too generic to claim.
        "patient ref", "patientref", "patient reference", "pt ref",
        "uhid ref", "mrn ref", "patient id ref", "patient uhid ref",
        "ref uhid", "ref mrn", "patient link", "linked patient",
    })),
    # Episode / encounter numbering (admission, OPD visit, case file).
    "episode_id": (FieldRole.PATIENT_IDENTIFIER, frozenset({
        "ipd no", "ipd number", "ip no", "ip number", "opd no", "opd number",
        "adm no", "admission no", "admission number", "admission id",
        "case no", "case number", "case id", "encounter id", "encounter no",
        "visit id", "visit no", "episode id", "episode no", "ipdno", "opdno",
        "भर्ती संख्या",
    })),
    # Specimen / report numbering. Still a patient-linkable identifier (a
    # lab accession number identifies one person's sample), so it is
    # tokenized, not retained -- just in its own namespace.
    "specimen_id": (FieldRole.PATIENT_IDENTIFIER, frozenset({
        "lab no", "lab number", "sample no", "sample id", "sampleid",
        "accession no", "accession number", "report no", "token no", "barcode",
    })),
    # Insurance / payer identifiers: a distinct scheme from a clinical UHID,
    # so they tokenize in their own namespace. Scored as INSURANCE_ID.
    "insurance_id": (FieldRole.PATIENT_IDENTIFIER, frozenset({
        "insurance id", "insurance no", "insurance number", "insurance policy",
        "policy no", "policy number", "policy id", "policyno", "pol no",
        "pmjay id", "pmjay", "pmjay_id", "ayushman id", "ayushman card",
        "member id", "card no", "card number", "scheme id", "scheme no",
        "esic no", "esi no", "cghs no", "payer id", "insurer", "insurer id",
        "tpa id", "tpa no", "beneficiary no", "beneficiary number",
        "बीमा संख्या", "पॉलिसी संख्या",
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
        "mob", "mob no", "cell", "cell no", "cell number", "whatsapp",
        "whatsapp no", "alt phone", "alternate phone", "alternate contact",
        "ph", "ph no", "tel", "tel no", "landline", "contact details",
        "मोबाइल", "फोन", "दूरभाष", "संपर्क",
        "mobile no", "mobail", "sampark", "sampark no", "door bhash",
    })),
    "email": (FieldRole.CONTACT, frozenset({"email", "email id", "e-mail", "mail id"})),
    "dob": (FieldRole.DATE, frozenset({
        "dob", "date of birth", "birth date", "d.o.b", "d.o.b.",
        "birthdate", "date of birth (dd/mm/yyyy)", "जन्म तिथि", "जन्मतिथि",
        # Romanised Hindi: "janm tithi" / "janam din" are birth dates. Missing
        # these published 30 real DOBs verbatim from one Hinglish TSV.
        "janm tithi", "janam tithi", "janm din", "janam din", "janmtithi",
        "janm dinank", "janam dinank",
    })),
    # An explicitly stated age. Carried as a DATE role so it travels through
    # the same per-patient generalization machinery as a DOB: the organizer's
    # own guidance is to publish a BAND ("age 45-49"), not the exact integer,
    # because the attacker's voter-roll-style registry carries age and an
    # exact age plus district plus gender is a strong re-identification key.
    "age": (FieldRole.DATE, frozenset({
        "age", "age years", "age in years", "age (years)", "patient age",
        "umar", "umr", "aayu", "ayu", "varsh", "उम्र", "आयु", "वर्ष",
    })),
    "admission_date": (FieldRole.DATE, frozenset({
        "admission date", "date of admission", "admitted on", "admit date",
    })),
    "discharge_date": (FieldRole.DATE, frozenset({
        "discharge date", "date of discharge", "discharged on",
    })),
    "encounter_date": (FieldRole.DATE, frozenset({
        "encounter date", "visit date", "consultation date", "date of visit",
        "registered", "registration date", "date of registration", "registered on",
        "reg date", "visit on", "opd date",
    })),
    "specimen_date": (FieldRole.DATE, frozenset({
        "specimen date", "collection date", "sample date", "date of collection",
    })),
    "report_date": (FieldRole.DATE, frozenset({
        "report date", "date of report", "result date", "reported on",
    })),
    "address": (FieldRole.FINE_ADDRESS, frozenset({
        "address", "residential address", "full address", "home address",
        "addr", "permanent address", "present address", "पता", "निवास",
        # Romanised Hindi. The whole address cell is a FINE_ADDRESS, which is
        # what finally removes the unlabelled components inside it (house
        # number, mohalla, landmark) that no in-line label could anchor.
        "pata", "poora pata", "niwas", "niwas sthan", "ghar ka pata",
    })),
    # A street line can co-exist with a full address column.
    "street": (FieldRole.FINE_ADDRESS, frozenset({
        "street", "street address", "address line", "address line 1", "address 1",
    })),
    "ward": (FieldRole.FINE_ADDRESS, frozenset({"ward", "ward no", "ward number"})),
    "house": (FieldRole.FINE_ADDRESS, frozenset({
        "house no", "house number", "h no", "h.no", "home no",
    })),
    "village": (FieldRole.FINE_ADDRESS, frozenset({
        "village", "village name", "gram", "gaon", "vill",
        "village/mohalla", "village / mohalla", "gram/mohalla",
        "hamlet", "panchayat", "gram panchayat", "ग्राम", "गाँव", "गांव",
        "gaon", "gram", "gaav", "basti", "tola", "majra", "mauza",
    })),
    # A town/city column can co-exist with a village column in the same
    # export, so it gets its own canonical (same fine-geography role).
    "city": (FieldRole.FINE_ADDRESS, frozenset({
        "city", "town", "place", "शहर", "नगर",
    })),
    "locality": (FieldRole.FINE_ADDRESS, frozenset({
        "locality", "mohalla", "gram mohalla", "colony", "area", "gali",
        "mohalla/colony", "muhalla", "ward/mohalla", "मोहल्ला", "कॉलोनी",
    })),
    "landmark": (FieldRole.FINE_ADDRESS, frozenset({
        "landmark", "near landmark", "nearby landmark", "nikat", "ke paas",
        "निकट", "लैंडमार्क", "pehchan chinh",
    })),
    # Two DISTINCT coarse levels, deliberately not one canonical. An export
    # routinely carries both a Tehsil and a District column (the practice
    # registration CSV does), and folding them together made two columns
    # claim one field. They are also different privacy levels: a tehsil is
    # the finer of the two, so the small-cohort ladder widens tehsil -> away
    # while KEEPING the district, which is the level `geography_level:
    # district` says is retained and which the answer key lists among the
    # values that must survive.
    "tehsil": (FieldRole.COARSE_GEOGRAPHY, frozenset({
        "tehsil", "tahsil", "taluk", "taluka", "block", "subdivision",
        "तहसील", "ब्लॉक", "tahseel", "vikas khand", "blok",
    })),
    "district": (FieldRole.COARSE_GEOGRAPHY, frozenset({
        "district", "distt", "dist", "zila", "jila", "state",
        "जिला", "जनपद", "राज्य", "jilaa", "zilaa", "rajya", "janpad",
    })),
    "pin": (FieldRole.FINE_ADDRESS, frozenset({
        "pin", "pincode", "pin code", "postal code", "zip", "zipcode", "zip code",
        "post code", "पिन", "पिन कोड", "pin sankhya", "dak code",
    })),
}


# Identifier schemes that name an EVENT, not a person: a specimen number, an
# admission number, a bill/claim number. They are still patient-linkable (so
# they are tokenized, never published) but they must never act as an identity
# ANCHOR, because one patient legitimately has many of them.
#
# This distinction matters because identity clustering vetoes a merge when two
# clusters hold different values for the same (issuer, scheme) pair. Treating
# an event id as a patient id turns "this patient had two blood tests" into a
# hard identifier contradiction and refuses to link their records -- measured
# on the practice data, every extra event-level scheme cost real recall. A
# patient-level scheme (uhid / aadhaar / abha) keeps full veto power, which is
# what still stops two different people from being merged.
EVENT_SCOPED_SCHEMES = frozenset({"specimen_id", "episode_id", "billing_id", "local_id"})


def normalize_header(raw: str) -> str:
    spaced = _CAMEL_BOUNDARY.sub(" ", raw)
    return " ".join(spaced.strip().lower().replace("_", " ").split())


# A possessive ("Patient's Name") and a parenthetical format hint
# ("DOB (MM/DD/YYYY)", "Registered (dd-mm-yyyy)") are decoration around an
# otherwise-known label. Stripped before the squashed comparison below so
# the column still resolves to its real role instead of falling through to
# OTHER -- which means RETAIN, i.e. published verbatim. The organizer's own
# guidance warns that hidden-dataset column names differ from the practice
# ones ("Patient's Name", "Contact#", "Gram/Mohalla"), so this is a
# correctness requirement for unseen data, not cosmetic.
_POSSESSIVE = re.compile(r"['’]s\b")
_PARENTHETICAL = re.compile(r"\([^)]*\)")
_NON_ALNUM = re.compile(r"[^0-9a-zऀ-ॿ]+")


def _squash(text: str) -> str:
    """Punctuation-insensitive comparison key: "d.o.b" / "DOB" / "dob:" and
    "H.No" / "h no" / "house_no" collapse onto one another."""
    text = _POSSESSIVE.sub("", text.lower())
    text = _PARENTHETICAL.sub(" ", text)
    return _NON_ALNUM.sub("", text)


# Built once at import: squashed alias -> (canonical, role). A squashed
# collision between two different canonical names would make resolution
# order-dependent, so the first definition wins and later ones are skipped
# (HEADER_ALIASES is an ordered dict literal, so this is deterministic).
_SQUASHED_INDEX: dict[str, tuple[str, FieldRole]] = {}
for _canonical, (_role, _aliases) in HEADER_ALIASES.items():
    for _alias in _aliases:
        _SQUASHED_INDEX.setdefault(_squash(_alias), (_canonical, _role))


def resolve_header(raw: str) -> tuple[str | None, FieldRole]:
    """Returns (canonical_name_or_None, role). Unknown headers keep OTHER role.

    Three passes, most literal first:
      1. the exact normalized header against the alias sets;
      2. each "/"-separated part, so a compound column the practice data
         never used ("Village/Mohalla", "Contact/Phone") still resolves;
      3. a punctuation/possessive/parenthetical-insensitive squashed key,
         which is what makes "Patient's Name", "Contact#" and
         "DOB (MM/DD/YYYY)" resolve on unseen data.
    """
    norm = normalize_header(raw)
    for canonical, (role, aliases) in HEADER_ALIASES.items():
        if norm in aliases:
            return canonical, role
    if "/" in norm:
        for part in (p.strip() for p in norm.split("/")):
            if not part:
                continue
            for canonical, (role, aliases) in HEADER_ALIASES.items():
                if part in aliases:
                    return canonical, role
    squashed = _squash(norm)
    if squashed:
        hit = _SQUASHED_INDEX.get(squashed)
        if hit is not None:
            return hit
        if "/" in norm:
            for part in norm.split("/"):
                hit = _SQUASHED_INDEX.get(_squash(part))
                if hit is not None:
                    return hit
    return _resolve_head_noun(norm)


# Container nouns that mean nothing without their qualifier. "Name", "ID" and
# "Number" are heads of both patient fields and non-patient ones ("Test Name",
# "Lab ID", "Bill Number"), so a tail consisting of one of these alone cannot
# decide a role -- and guessing wrong here redacts clinical content: "test
# name" would resolve to patient_name and every lab test in the corpus would
# be destroyed. The full header, where the qualifier is present and decides
# the answer, is already tried by passes 1-3.
_GENERIC_HEAD_NOUNS = frozenset({
    "name", "id", "no", "nos", "number", "num", "code", "value", "type",
    "ref", "reference", "key", "label", "detail", "details", "info", "data",
    "naam", "sankhya", "नाम", "संख्या", "क्रमांक",
})


def _resolve_head_noun(norm: str) -> tuple[str | None, FieldRole]:
    """Fourth and last pass: the longest known alias that forms the TAIL of a
    qualified header. "hospital_uhid", "primary_contact_number" and
    "patient_dob" are an aliased field with a qualifier bolted on the front.

    Matched from the tail because English and Hinglish compounds put the head
    noun last: in "hospital uhid" the field is a uhid and "hospital" qualifies
    it. Matching anywhere in the header instead would read "referring hospital
    name" as a patient name, because "name" appears in it.

    This is a real leak channel, not a hypothetical: `lab/report_00005.json`
    stores the patient's hospital number under the key `hospital_uhid`, which
    matched no alias and therefore kept FieldRole.OTHER -- and OTHER means
    retain, i.e. published verbatim. Sixteen MRNs left the building that way.
    The organizer's own guidance warns that hidden-dataset column names differ
    from the practice ones, so resolving a qualified header is a requirement
    for unseen data rather than an accommodation of this corpus.

    Longest tail wins, so "patient id" beats "id" when both are known.
    """
    tokens = norm.split()
    for start in range(1, len(tokens)):  # starts at 1: the whole header was
        tail_tokens = tokens[start:]     # already tried by passes 1-3
        if len(tail_tokens) == 1 and tail_tokens[0] in _GENERIC_HEAD_NOUNS:
            continue
        tail = " ".join(tail_tokens)
        hit = _SQUASHED_INDEX.get(_squash(tail))
        if hit is not None:
            return hit
    return None, FieldRole.OTHER


def is_field_label(token: str) -> bool:
    """True when a word is one of the field LABELS this module knows, in any of
    the scripts it knows them in -- "Name", "नाम", "Naam", "आयु", "ग्राम".

    Free-text detection needs this because a label word is not a value. In a
    Hindi OPD slip ("नाम: रीना निषाद   आयु: 65 वर्ष / ग्राम: भीमनगर") the
    labels themselves transliterate onto real-name consonant skeletons, so the
    cross-script gazetteer route claimed नाम, आयु, वर्ष and ग्राम as patient
    names. Every slip in the corpus carries the same four labels, which handed
    identity resolution four agreeing "name" tokens shared by every patient --
    measured on the practice data, that merged ten different people into one
    pseudonym.

    Derived from HEADER_ALIASES rather than from a separate stoplist, so a
    label this module learns for column mapping is automatically known to free
    text too, and the two cannot drift apart.
    """
    squashed = _squash(token)
    return bool(squashed) and squashed in _SQUASHED_INDEX
