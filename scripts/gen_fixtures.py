#!/usr/bin/env python3
"""Generates the static Phase 1 synthetic fixture tree under tests/fixtures/.

All data is synthetic/fabricated for testing. Encoded fixtures (UTF-16, BOM)
are written as bytes directly so the encoding adapter is exercised for real,
not simulated.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def w(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if encoding == "utf-8":
        path.write_bytes(text.encode("utf-8"))
    elif encoding == "utf-8-sig":
        path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    elif encoding == "utf-16":
        path.write_bytes(text.encode("utf-16"))  # includes BOM
    else:
        raise ValueError(encoding)


def wj(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    dev = ROOT / "dev"
    held = ROOT / "heldout"

    # --- CSV / TSV basics -------------------------------------------------
    w(dev / "csv_basic" / "registration.csv",
      'Patient Name,UHID,Phone,Address\n'
      'Ramesh Kumar,UH00A1,9876543210,"C/O Suresh, Ward 5, Lucknow"\n'
      'Sita Devi,007,9123456780,"Mohalla Rampur, Tehsil Sadar"\n')

    w(dev / "csv_basic" / "multiline_quoted.csv",
      'Patient Name,Notes\n'
      '"Kiran Shah","Line one of note.\nLine two of note, continued."\n')

    w(dev / "csv_basic" / "tsv_sample.tsv",
      "Patient Name\tUHID\tPhone\n"
      "Anjali Verma\tUH0099\t9988776655\n")

    w(dev / "csv_malformed" / "bad_rows.csv",
      "Patient Name,UHID,Phone\n"
      "Deepak Singh,UH01,9999999999,EXTRA_FIELD\n"
      "Pooja Rani,UH02\n")

    w(dev / "csv_malformed" / "duplicate_headers.csv",
      "Patient Name,Patient Name,UHID\n"
      "Anil Kapoor,Anil K,UH03\n")

    w(dev / "csv_malformed" / "ambiguous_alias_headers.csv",
      "Patient Name,Pt Name,UHID\n"
      "Vikram Rathore,V. Rathore,UH04\n")

    # --- JSON basics --------------------------------------------------------
    wj(dev / "json_basic" / "object_record.json", {
        "patientName": "Neha Joshi", "uhid": "UH0501",
        "address": {"ward": "Ward 9", "district": "Kanpur"},
    })
    wj(dev / "json_basic" / "array_records.json", [
        {"patientName": "Ravi Teja", "uhid": "UH0601"},
        {"patientName": "Divya Rao", "uhid": "UH0602"},
    ])
    wj(dev / "json_basic" / "wrapped_records.json", {
        "meta": {"source": "hospitalA"},
        "records": [
            {"patientName": "Sunil Mehta", "uhid": "UH0701"},
        ],
    })
    wj(dev / "json_basic" / "nested_precision.json", {
        "patientName": "Arvind Nair", "uhid": "00123",
        "labs": {"hba1c": 8.2, "bigId": 123456789012345678901234567890},
    })

    # duplicate key: hand-written since json.dumps can't produce duplicates
    w(dev / "json_malformed" / "duplicate_key.json",
      '{"patientName": "Test One", "patientName": "Test Two", "uhid": "UH08"}')
    w(dev / "json_malformed" / "syntax_error.json", '{"patientName": "Oops",}')

    # --- TXT clinical notes --------------------------------------------------
    w(dev / "txt_notes" / "clinical_semantics.txt",
      "Registration\n"
      "Address: C/O Ramesh, Ward 5\n"
      "OPD: C/O fever for 5 days\n"
      "K/C/O diabetes\n"
      "H/O hypertension\n"
      "Dr. Ramesh advised admission.\n"
      "Prescription: Ramipril 5 mg twice daily\n"
      "BP 140/90 and 150/90\n"
      "HbA1c 8.2%\n")

    w(dev / "txt_notes" / "provider_patient_collision.txt",
      "Consultant: Dr. Ramesh\n"
      "Patient: Ramesh Chandra was seen in OPD today.\n")

    w(held / "cue_only_names" / "unseen_name_cue.txt",
      "Zubair sahab ka BP 130/85 hai, dawai di gayi.\n"
      "Smt. Kalawati was advised rest.\n")

    # --- FHIR -----------------------------------------------------------------
    wj(dev / "fhir" / "patient_encounter.json", {
        "resourceType": "Patient",
        "id": "pat-1",
        "identifier": [{"system": "https://hospitalA.example/uhid", "value": "UH0901"}],
        "name": [{"text": "Farhan Qureshi", "family": "Qureshi", "given": ["Farhan"]}],
        "birthDate": "1990-05-12",
        "telecom": [{"system": "phone", "value": "9090909090"}],
        "address": [{"line": ["House 12, Civil Lines"], "district": "Kanpur", "postalCode": "208001"}],
    })
    wj(dev / "fhir" / "bundle_collection.json", {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {
                "fullUrl": "urn:uuid:11111111-1111-1111-1111-111111111111",
                "resource": {
                    "resourceType": "Patient", "id": "pat-2",
                    "identifier": [{"system": "https://hospitalA.example/uhid", "value": "UH1001"}],
                    "name": [{"text": "Alok Pandey"}],
                    "birthDate": "1985-01-01",
                },
            },
            {
                "fullUrl": "urn:uuid:22222222-2222-2222-2222-222222222222",
                "resource": {
                    "resourceType": "Encounter", "id": "enc-2",
                    "subject": {"reference": "urn:uuid:11111111-1111-1111-1111-111111111111", "display": "Alok Pandey"},
                },
            },
            {
                "resource": {
                    "resourceType": "Observation", "id": "obs-2",
                    "subject": {"reference": "Patient/pat-2"},
                    "code": {"text": "Blood Pressure"},
                    "valueQuantity": {"value": 140, "unit": "mmHg"},
                },
            },
            {
                "resource": {
                    "resourceType": "Observation", "id": "obs-unresolved",
                    "subject": {"reference": "Patient/does-not-exist"},
                    "code": {"text": "HbA1c"},
                },
            },
        ],
    })

    # --- Arif corroborated: four scripts/abbreviations, should all link -----
    base = dev / "arif_corroborated"
    w(base / "registration.csv",
      "Patient Name,UHID,Phone\n"
      "Mohammad Arif,UHA001,9811122233\n")
    wj(base / "consultation.json", {
        "patientName": "मोहम्मद आरिफ़", "uhid": "UHA001",
        "visit": {"complaint": "fever", "doctor": "Dr. Singh"},
    })
    w(base / "discharge.txt",
      "Discharge Summary\n"
      "Patient: Md. Arif, UHID UHA001\n"
      "Stay: admitted 12 Oct 1990, discharged 17 Oct 1990.\n")
    w(base / "opd_note.txt",
      "OPD\n"
      "Arif sahab ka BP 150/90 hai, Ramipril 5 mg twice daily diya gaya.\n")

    # --- Arif ambiguous: a bare mention with no corroborating evidence -------
    w(dev / "arif_ambiguous" / "opd_note_only.txt",
      "OPD\nArif sahab aaj follow-up ke liye aaye.\n")

    # --- A different Arif: same first name, must remain separate ------------
    w(dev / "arif_different" / "registration.csv",
      "Patient Name,UHID,Phone,Address\n"
      "Arif Mansoori,UHZ999,9000011122,\"Village Barwa, Tehsil Mau\"\n")

    # --- Meera across CSV/JSON/TXT, same UHID -> should link -----------------
    base = dev / "meera_corroborated"
    w(base / "registration.csv", "Patient Name,UHID,Phone\nMeera Sharma,UHM001,9811100011\n")
    wj(base / "consultation.json", {"patientName": "मीरा", "uhid": "UHM001"})
    w(base / "discharge.txt", "Discharge Summary\nPatient: Mira, UHID UHM001\n")

    # --- Same UHID, different issuers: must NOT auto-merge -------------------
    # Hospital A has no explicit issuer (-> its own conservative per-file
    # unknown scope, see identity.build_candidate_features) and hospital B's
    # FHIR resource carries its own explicit identifier.system -- two
    # different scopes regardless, so this must never merge.
    w(dev / "uhid_cross_issuer_hospitalA" / "hospitalA.csv", "Patient Name,UHID,Phone\nSunita Rao,UH500,9812345670\n")
    wj(dev / "uhid_cross_issuer_hospitalB" / "hospitalB_fhir.json", {
        "resourceType": "Patient", "id": "pat-hb",
        "identifier": [{"system": "https://hospitalB.example/uhid", "value": "UH500"}],
        "name": [{"text": "Geeta Rao"}],
        "telecom": [{"system": "phone", "value": "9000000001"}],
    })

    # --- Finding 1 regression: explicit source-system scoping ----------------
    # Two plain CSVs, no facility field, no explicit scheme issuer, and (for
    # the "unknown" pair) no --source-system config either -- the direct
    # reproduction of the original bug report (a repeated local id across
    # independently sourced files must never auto-merge). The "configured_*"
    # pairs show the positive/negative sides of a deliberate contract: see
    # gate.phase1.DEV_SOURCE_SYSTEM_MAP for the configuration applied when
    # these fixtures are run through Gate 1 / eval_gate1.py.
    w(dev / "source_scope_unknown" / "hospitalA" / "records.csv", "Patient Name,UHID\nAsha Devi,LOCAL-001\n")
    w(dev / "source_scope_unknown" / "hospitalB" / "records.csv", "Patient Name,UHID\nNoor Fatima,LOCAL-001\n")
    w(dev / "source_scope_configured_shared" / "hospitalA" / "records.csv", "Patient Name,UHID\nKiran Mehta,LOCAL-200\n")
    w(dev / "source_scope_configured_shared" / "hospitalB" / "records.csv", "Patient Name,UHID\nKiran Mehta,LOCAL-200\n")
    w(dev / "source_scope_configured_distinct" / "hospitalA" / "records.csv", "Patient Name,UHID\nVikas Oberoi,LOCAL-300\n")
    w(dev / "source_scope_configured_distinct" / "hospitalB" / "records.csv", "Patient Name,UHID\nNilima Bhatt,LOCAL-300\n")

    # --- Finding 1 regression: national scheme consistent across routes ------
    # The same Aadhaar value, once in a structured CSV field and once in a
    # free-text note -- must resolve to the same (issuer, scheme) scope and
    # exact-link, proving the structured/free-text issuer fix actually holds.
    w(dev / "aadhaar_consistency" / "registration.csv", "Patient Name,Aadhaar\nFarida Shah,411122223333\n")
    w(dev / "aadhaar_consistency" / "note.txt", "OPD note\nFarida Shah aadhaar 411122223333 verified at desk.\n")

    # --- Finding 2 regression: FHIR subject reference -> patient association -
    wj(dev / "fhir_patient_link" / "two_patients_bundle.json", {
        "resourceType": "Bundle", "type": "collection",
        "entry": [
            {"resource": {
                "resourceType": "Patient", "id": "pA",
                "identifier": [{"system": "https://hospX.example/uhid", "value": "UHPA1"}],
                "name": [{"text": "Deepika Rao"}],
            }},
            {"resource": {"resourceType": "Encounter", "id": "eA", "subject": {"reference": "Patient/pA"}}},
            {"resource": {
                "resourceType": "Patient", "id": "pB",
                "identifier": [{"system": "https://hospX.example/uhid", "value": "UHPB1"}],
                "name": [{"text": "Rohan Verma"}],
            }},
            {"resource": {
                "resourceType": "Observation", "id": "oB",
                "subject": {"reference": "Patient/pB"}, "code": {"text": "HbA1c"},
            }},
        ],
    })
    wj(dev / "fhir_patient_link" / "performer_not_patient.json", {
        "resourceType": "Bundle", "type": "collection",
        "entry": [
            {"resource": {"resourceType": "Practitioner", "id": "doc1", "name": [{"text": "Dr. Alka Rao"}]}},
            {"resource": {
                "resourceType": "Observation", "id": "obsNoSubj",
                "performer": {"reference": "Practitioner/doc1"}, "code": {"text": "Blood Pressure"},
            }},
        ],
    })
    wj(dev / "fhir_patient_link" / "ambiguous_subject_bundle.json", {
        "resourceType": "Bundle", "type": "collection",
        "entry": [
            {"resource": {
                "resourceType": "Patient", "id": "pX",
                "identifier": [{"system": "https://hospX.example/uhid", "value": "UHX1"}],
                "name": [{"text": "Farah Khan"}],
            }},
            {"resource": {
                "resourceType": "Patient", "id": "pY",
                "identifier": [{"system": "https://hospX.example/uhid", "value": "UHY1"}],
                "name": [{"text": "Rakesh Dutt"}],
            }},
            {"resource": {
                "resourceType": "Observation", "id": "obsAmbig",
                "subject": [{"reference": "Patient/pX"}, {"reference": "Patient/pY"}],
                "code": {"text": "Blood Pressure"},
            }},
        ],
    })
    wj(dev / "fhir_patient_link" / "duplicate_logical_id_bundle.json", {
        "resourceType": "Bundle", "type": "collection",
        "entry": [
            {"resource": {
                "resourceType": "Patient", "id": "dup",
                "identifier": [{"system": "https://hospX.example/uhid", "value": "UHDUP1"}],
                "name": [{"text": "Alpha One"}],
            }},
            {"resource": {
                "resourceType": "Patient", "id": "dup",
                "identifier": [{"system": "https://hospX.example/uhid", "value": "UHDUP2"}],
                "name": [{"text": "Beta Two"}],
            }},
            {"resource": {"resourceType": "Encounter", "id": "encDup", "subject": {"reference": "Patient/dup"}}},
        ],
    })

    # --- Finding 3 regression: contained references resolve per-owner --------
    wj(dev / "fhir_contained_scope" / "owner_scoped_contained.json", {
        "resourceType": "Bundle", "type": "collection",
        "entry": [
            {"resource": {
                "resourceType": "Encounter", "id": "encOwnerA",
                "contained": [{"resourceType": "Patient", "id": "elsewhere"}],
                "subject": {"reference": "#elsewhere"},
            }},
            {"resource": {
                "resourceType": "Observation", "id": "obsOther",
                "subject": {"reference": "#elsewhere"}, "code": {"text": "Blood Pressure"},
            }},
            {"resource": {
                "resourceType": "Encounter", "id": "encDupContained",
                "contained": [
                    {"resourceType": "Patient", "id": "dup"},
                    {"resourceType": "Patient", "id": "dup"},
                ],
                "subject": {"reference": "#dup"},
            }},
            {"resource": {"resourceType": "Encounter", "id": "encMissing", "subject": {"reference": "#missing"}}},
            {"resource": {
                "resourceType": "Encounter", "id": "encOwnerB",
                "contained": [{"resourceType": "Patient", "id": "elsewhere"}],
                "subject": {"reference": "#elsewhere"},
            }},
        ],
    })

    # --- Finding 4 regression: unsupported/malformed Bundle entries ----------
    wj(dev / "fhir_unsupported_entries" / "unsupported_only_bundle.json", {
        "resourceType": "Bundle", "type": "collection",
        "entry": [{"resource": {"resourceType": "Organization", "id": "org1", "name": "Some Clinic"}}],
    })
    wj(dev / "fhir_unsupported_entries" / "mixed_supported_unsupported_bundle.json", {
        "resourceType": "Bundle", "type": "collection",
        "entry": [
            {"resource": {
                "resourceType": "Patient", "id": "p1",
                "identifier": [{"system": "https://hospX.example/uhid", "value": "UHMX1"}],
                "name": [{"text": "Sana Iqbal"}],
            }},
            {"resource": {"resourceType": "Organization", "id": "org1", "name": "Some Clinic"}},
            {"resource": {"id": "noType1"}},
            "not-a-dict-entry",
            {},
        ],
    })
    wj(dev / "fhir_unsupported_entries" / "supported_empty_bundle.json", {
        "resourceType": "Bundle", "type": "collection", "entry": [],
    })

    # --- Shared household phone, different people (must not merge alone) -----
    w(dev / "household_shared" / "family.csv",
      "Patient Name,UHID,Phone,Address\n"
      "Rajesh Gupta,UH601,9811177711,\"Flat 4, Green Park\"\n"
      "Priya Gupta,UH602,9811177711,\"Flat 4, Green Park\"\n")

    # --- Conflicting authoritative IDs: same issuer (one file = one hospital
    # scope here), same name+phone, but a contradicting UHID -> must veto.
    w(dev / "conflicting_ids" / "same_hospital.csv",
      "Patient Name,UHID,Phone\n"
      "Manoj Tiwari,UH701,9822233440\n"
      "Manoj Tiwari,UH702,9822233440\n")

    # --- A-B-C bridge that must not merge due to a conflict at C -------------
    # a and b share no UHID (so they'd only fuzzy-match on name+phone); b and c
    # share no UHID either; but a and c, in the SAME issuer scope, disagree --
    # the cluster-level check must catch this even though no single pairwise
    # edge directly compares a against c.
    w(dev / "bridge_conflict" / "same_hospital.csv",
      "Patient Name,UHID,Phone\n"
      "Sandeep Yadav,UH801,9833344550\n"
      "Sandeep Yadav,,9833344550\n"
      "Sandeep Yadav,UH802,9833344550\n")

    # --- Held-out: names absent from the gazetteer, near strong cues ---------
    w(held / "gazetteer_absent" / "cue_supported.txt",
      "Zufishan sahab ka follow-up visit hua.\nS/o Qadeer Ahmed aaya tha.\n")
    w(held / "gazetteer_absent" / "no_cue_no_detection.txt",
      "Zufishan ne dawai li thi lekin koi context nahi diya gaya.\n")

    # --- Encoding fixtures ----------------------------------------------------
    w(dev / "encodings" / "strict_utf8.csv", "Patient Name,UHID\nAditi Sen,UH901\n", "utf-8")
    w(dev / "encodings" / "bom_utf8.csv", "Patient Name,UHID\nBinay Sen,UH902\n", "utf-8-sig")
    w(dev / "encodings" / "bom_utf16.csv", "Patient Name,UHID\nChaitali Sen,UH903\n", "utf-16")

    # --- Unsupported / empty --------------------------------------------------
    (dev / "unsupported" / "scan.pdf").parent.mkdir(parents=True, exist_ok=True)
    (dev / "unsupported" / "scan.pdf").write_bytes(b"%PDF-1.4 fake binary content")
    (dev / "unsupported" / "empty.csv").parent.mkdir(parents=True, exist_ok=True)
    (dev / "unsupported" / "empty.csv").write_bytes(b"")
    (dev / "unsupported" / "empty.json").write_bytes(b"")
    (dev / "unsupported" / "empty.txt").write_bytes(b"")

    print(f"Fixtures written under {ROOT}")


if __name__ == "__main__":
    main()
