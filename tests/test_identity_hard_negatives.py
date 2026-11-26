"""Hard negatives for patient linkage: pairs that must NEVER become one patient.

Every case here is a pair of records that a name-similarity system merges and a
correct one refuses. They are kept together, and kept end-to-end (each builds a
real corpus and runs the real pipeline), because the failure they guard against
is silent: a wrong merge produces one confident pseudonym rather than an error,
and it costs patient privacy -- two people's records exposed to each other --
not just a metric.

Each test states the single piece of evidence that separates the pair, so a
future change that removes that evidence fails here with the reason named.
"""

from pathlib import Path

from nijsetu.config import AppConfig
from nijsetu.plan import run_pipeline


def _corpus(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _patients(root: Path) -> dict[str, tuple[str | None, str]]:
    """file path -> (cluster_id, link_status) for every record in the corpus."""
    plan = run_pipeline(root, AppConfig())
    return {r["file_path"]: (r["cluster_id"], r["link_status"]) for r in plan.private["records"]}


def _merged(result: dict[str, tuple[str | None, str]], a: str, b: str) -> bool:
    (cluster_a, status_a), (cluster_b, status_b) = result[a], result[b]
    return status_a == "ACCEPTED" and status_b == "ACCEPTED" and cluster_a == cluster_b


# --- same name, different person -------------------------------------------

def test_identical_full_names_separated_by_different_spouses(tmp_path):
    """The organizer's planted homonym: two Sukhwinder Kaurs at two hospitals.
    Names agree completely and the UHIDs sit in different issuer namespaces, so
    the ONLY evidence is the husband's name."""
    root = _corpus(tmp_path / "in", {
        "regA/patients.csv": (
            "Patient Name,UHID,Husband Name\n"
            "Sukhwinder Kaur,JJH-0077058,W/o Sukhwinder Singh\n"
        ),
        "regB/patients.csv": (
            "Patient Name,UHID,Husband Name\n"
            "Sukhwinder Kaur,SNJ-0264423,W/o Kuldeep Singh\n"
        ),
    })
    result = _patients(root)
    assert not _merged(result, "regA/patients.csv", "regB/patients.csv")


def test_identical_full_names_separated_by_different_phones(tmp_path):
    root = _corpus(tmp_path / "in", {
        "a/patients.csv": "Patient Name,UHID,Mobile\nGeeta Chauhan,AAA-1001,9811122233\n",
        "b/patients.csv": "Patient Name,UHID,Mobile\nGeeta Chauhan,BBB-2002,9000011122\n",
    })
    assert not _merged(_patients(root), "a/patients.csv", "b/patients.csv")


def test_identical_full_names_separated_by_different_birth_years(tmp_path):
    root = _corpus(tmp_path / "in", {
        "a/patients.csv": "Patient Name,UHID,DOB\nAnita Sharma,AAA-1001,06-01-1999\n",
        "b/patients.csv": "Patient Name,UHID,DOB\nAnita Sharma,BBB-2002,14-03-1971\n",
    })
    assert not _merged(_patients(root), "a/patients.csv", "b/patients.csv")


def test_same_facility_series_different_numbers_do_not_merge(tmp_path):
    """Two different numbers out of ONE hospital's numbering series are two
    patients, even across files the issuer map does not describe."""
    root = _corpus(tmp_path / "in", {
        "reg/patients.csv": "Patient Name,UHID\nReena Nishad,JJH-0076928\n",
        "lab/bulk.csv": "Patient Name,UHID\nReena Nishad,JJH-0077112\n",
    })
    assert not _merged(_patients(root), "reg/patients.csv", "lab/bulk.csv")


# --- shared contact details, different people ------------------------------

def test_family_sharing_one_phone_is_not_one_patient(tmp_path):
    """A household phone is a household signal, not an identity. Two siblings
    on one number must stay two patients."""
    root = _corpus(tmp_path / "in", {
        "reg/a.csv": "Patient Name,UHID,Mobile\nRavi Verma,AAA-1001,9811122233\n",
        "reg/b.csv": "Patient Name,UHID,Mobile\nSunita Verma,AAA-1002,9811122233\n",
    })
    assert not _merged(_patients(root), "reg/a.csv", "reg/b.csv")


def test_shared_address_and_surname_is_not_one_patient(tmp_path):
    root = _corpus(tmp_path / "in", {
        "reg/a.csv": "Patient Name,UHID,Address\nMohit Yadav,AAA-1001,\"Gopalganj Khurd, Nawabganj\"\n",
        "reg/b.csv": "Patient Name,UHID,Address\nPriya Yadav,AAA-1002,\"Gopalganj Khurd, Nawabganj\"\n",
    })
    assert not _merged(_patients(root), "reg/a.csv", "reg/b.csv")


# --- recycled local identifiers across unrelated sources -------------------

def test_same_local_id_at_two_unconfigured_hospitals_does_not_merge(tmp_path):
    """Two hospitals both numbering a patient "LOCAL-001". Nothing declares
    them to be one issuer, so the repeated value is a coincidence."""
    root = _corpus(tmp_path / "in", {
        "hospA/patients.csv": "Patient Name,UHID\nAsha Devi,LOCAL-001\n",
        "hospB/patients.csv": "Patient Name,UHID\nNoor Fatima,LOCAL-001\n",
    })
    assert not _merged(_patients(root), "hospA/patients.csv", "hospB/patients.csv")


def test_generic_scheme_prefix_is_not_a_facility_series(tmp_path):
    """"UHID-..." names no facility, so two patients numbered that way at two
    hospitals must not be read as one series -- and equally must not merge."""
    root = _corpus(tmp_path / "in", {
        "hospA/patients.csv": "Patient Name,UHID\nKamal Rajput,UHID-010610\n",
        "hospB/patients.csv": "Patient Name,UHID\nKamal Rajput,UHID-884413\n",
    })
    result = _patients(root)
    assert not _merged(result, "hospA/patients.csv", "hospB/patients.csv")


def test_one_patient_many_specimens_is_not_an_identifier_conflict(tmp_path):
    """The mirror image: an EVENT-level id (specimen/episode/bill) repeats per
    visit, so it must never veto a genuine merge. Guards the fix from costing
    recall as well as the hard negatives above costing precision."""
    root = _corpus(tmp_path / "in", {
        "lab/results.csv": (
            "Patient Name,UHID,Specimen ID,Test,Result\n"
            "Shanti Choudhary,AAA-1001,SPC-900001,Hb,11.2\n"
            "Shanti Choudhary,AAA-1001,SPC-900002,Hb,11.6\n"
        ),
    })
    plan = run_pipeline(root, AppConfig())
    clusters = {r["cluster_id"] for r in plan.private["records"] if r["link_status"] == "ACCEPTED"}
    assert len(clusters) == 1, "two specimens of one patient must stay one patient"


# --- adversarial transitive bridges ----------------------------------------

def test_bare_given_name_does_not_bridge_two_different_patients(tmp_path):
    """A note saying only "Sukhwinder ji" fits both patients equally. Linking it
    to either would silently attach one person's note to the other's record, so
    the system must abstain rather than guess."""
    root = _corpus(tmp_path / "in", {
        "regA/patients.csv": "Patient Name,UHID,Mobile\nSukhwinder Kaur,AAA-1001,9811122233\n",
        "regB/patients.csv": "Patient Name,UHID,Mobile\nSukhwinder Devi,BBB-2002,9000011122\n",
        "notes/ward.txt": "NURSING NOTES - Ward 8\nSukhwinder ji ka BP 109/76 hai.\n",
    })
    result = _patients(root)
    assert not _merged(result, "regA/patients.csv", "regB/patients.csv")
    bridged = (
        _merged(result, "notes/ward.txt", "regA/patients.csv")
        and _merged(result, "notes/ward.txt", "regB/patients.csv")
    )
    assert not bridged


def test_a_conflicting_pair_is_not_joined_through_a_compatible_middle(tmp_path):
    """A-B and B-C both look acceptable while A-C actively conflict. A merge is
    a claim about every record on both sides, so the cluster must not form."""
    root = _corpus(tmp_path / "in", {
        "a/rec.csv": "Patient Name,UHID,Mobile,DOB\nRekha Singh,AAA-1001,9811122233,06-01-1999\n",
        "b/rec.csv": "Patient Name,UHID\nRekha Singh,BBB-2002\n",
        "c/rec.csv": "Patient Name,UHID,Mobile,DOB\nRekha Singh,CCC-3003,9000011122,14-03-1971\n",
    })
    result = _patients(root)
    assert not _merged(result, "a/rec.csv", "c/rec.csv")


def test_shared_facility_banner_does_not_bridge_a_hospital_s_patients(tmp_path):
    """Every document a hospital issues repeats its name, and hospital names
    contain real given names ("Jeevan Jyoti"). Treating the banner as a patient
    name hands every patient of that hospital the same name token."""
    root = _corpus(tmp_path / "in", {
        "rx/one.txt": "Jeevan Jyoti Charitable Hospital\nPatient: Reena Nishad\nTab Paracetamol 650mg\n",
        "rx/two.txt": "Jeevan Jyoti Charitable Hospital\nPatient: Mohammad Shahid\nTab Pantoprazole 40mg\n",
    })
    assert not _merged(_patients(root), "rx/one.txt", "rx/two.txt")


def test_shared_hindi_field_labels_do_not_bridge_patients(tmp_path):
    """Hindi field labels transliterate onto real-name consonant skeletons, and
    every document in a corpus carries the same labels. Claiming them as names
    gives every patient an identical set of "name" tokens -- measured on the
    practice data, that merged ten different people into one pseudonym.

    The two slips below share every word EXCEPT the patient's name, the village
    and the durations, which is exactly the shape of a real OPD slip.
    """
    root = _corpus(tmp_path / "in", {
        "rx/one.txt": (
            "नाम: रीना निषाद   आयु: 65 वर्ष\nग्राम: भीमनगर\nतारीख: 11-10-2026\n"
            "रीना निषाद को 6 दिन से बुखार है।\nTab Paracetamol 650mg\n"
        ),
        "rx/two.txt": (
            "नाम: मोहम्मद शाहिद   आयु: 39 वर्ष\nग्राम: मोहनगंज कलां\nतारीख: 03-04-2026\n"
            "मोहम्मद शाहिद को 9 दिन से बुखार है।\nInj Ceftriaxone 1g\n"
        ),
    })
    assert not _merged(_patients(root), "rx/one.txt", "rx/two.txt")


def test_two_family_members_sharing_a_surname_are_not_one_patient(tmp_path):
    """A household correlation is not an identity. These two share the surname
    Pandey and nothing else; one is written in Devanagari and one in Latin, so
    cross-script normalization makes their names look similar on top of it.
    Admitting a "same household" pair as a merge candidate merged exactly this
    pair on the practice data."""
    root = _corpus(tmp_path / "in", {
        "rx/one.txt": (
            "नाम: हरीश पांडे   आयु: 21 वर्ष\nग्राम: अमरगंज खुर्द\n"
            "हरीश पांडे को 8 दिन से बुखार है।\nTab Azithromycin 500mg\n"
        ),
        "reg/patients.csv": (
            "UHID,Patient Name,DOB,Relation,Relative Name,Village/Mohalla\n"
            "SNJ-26-064371,Kavita Pandey,17/08/1977,W/o,Mohan Pandey,Bhimganj\n"
        ),
    })
    assert not _merged(_patients(root), "rx/one.txt", "reg/patients.csv")


def test_a_shared_attending_physician_does_not_bridge_their_patients(tmp_path):
    """Every prescription a doctor signs carries their name. Reading it as a
    patient name gives every patient of that doctor the same two name tokens --
    the strongest possible false evidence."""
    root = _corpus(tmp_path / "in", {
        "rx/a.txt": "Patient: Suresh Verma\nTab Paracetamol 650mg\nDr. Harjeet Bedi (MD Medicine)\n",
        "rx/b.txt": "Patient: Kavita Pandey\nTab Amlodipine 5mg\nDr. Harjeet Bedi (MD Medicine)\n",
    })
    assert not _merged(_patients(root), "rx/a.txt", "rx/b.txt")
