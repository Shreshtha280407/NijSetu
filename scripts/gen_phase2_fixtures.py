#!/usr/bin/env python3
"""Regenerable synthetic Phase 2 fixtures -- deterministic, no real patient
data. Run this to recreate tests/fixtures/phase2/dev/ from scratch."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "phase2" / "dev"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def generate() -> None:
    # 1. The required worked case: one patient (Mohammad Arif / UHID AR001)
    #    across CSV + JSON + TXT, admission 12 Oct -> discharge 17 Oct (5 days),
    #    BP 140/90, HbA1c 8.2%, Ramipril 5 mg twice daily.
    write(ROOT / "core_patient" / "registration.csv",
          "Patient Name,UHID,Phone,Address,Admission Date\n"
          "Mohammad Arif,AR001,9876543210,\"C/O Ramesh, Ward 5, Lucknow\",2024-10-12\n")
    write(ROOT / "core_patient" / "consultation.json",
          json.dumps([{"patientName": "Mohammad Arif", "uhid": "AR001", "hba1c": "8.2%"}], ensure_ascii=False))
    write(ROOT / "core_patient" / "discharge.txt",
          "Patient: Mohammad Arif, UHID AR001\n"
          "Admission Date: 2024-10-12\n"
          "Discharge Date: 2024-10-17\n"
          "BP 140/90 on admission. HbA1c 8.2%. Ramipril 5 mg twice daily.\n"
          "K/C/O diabetes mellitus.\n")

    # 2. Small cohort: three patients, each a distinct age/geography/period
    #    combination -- must NOT be blanket-suppressed under balanced policy.
    for i, (name, uhid, age_dob, district) in enumerate([
        ("Priya Nair", "SC001", "1990-03-01", "Kannur"),
        ("Rahul Verma", "SC002", "1985-07-15", "Pune"),
        ("Sunita Devi", "SC003", "1972-11-20", "Patna"),
    ]):
        write(ROOT / "small_cohort" / f"patient_{i}.csv",
              f"Patient Name,UHID,DOB,District,Admission Date\n{name},{uhid},{age_dob},{district},2024-06-0{i+1}\n")

    # 3. Unresolved standalone: a bare cue-detected name, no identifiers at all.
    write(ROOT / "unresolved_standalone" / "note.txt",
          "OPD note.\nArif sahab came for routine checkup. BP 120/80.\n")

    # 4. Malformed + unsupported: explicit holds, never silently dropped.
    write(ROOT / "malformed_and_unsupported" / "bad.csv",
          "Patient Name,Patient Name,UHID\nX,Y,Z\n")
    write(ROOT / "malformed_and_unsupported" / "scan.pdf", "%PDF-1.4 not a real pdf\n")

    # 5. FHIR bundle: Patient + Encounter (subject ref) + Observation (BP).
    bundle = {
        "resourceType": "Bundle", "type": "collection",
        "entry": [
            {"fullUrl": "urn:uuid:11111111-1111-4111-8111-111111111111", "resource": {
                "resourceType": "Patient", "id": "pat-1",
                "identifier": [{"system": "urn:local:uhid", "value": "FH001"}],
                "name": [{"text": "Meera Sharma"}],
                "birthDate": "1988-05-20",
                "telecom": [{"system": "phone", "value": "9123456780"}],
                "address": [{"district": "Jaipur", "postalCode": "302001"}],
            }},
            {"fullUrl": "urn:uuid:22222222-2222-4222-8222-222222222222", "resource": {
                "resourceType": "Encounter", "id": "enc-1",
                "status": "finished", "class": {"code": "AMB"},
                "subject": {"reference": "urn:uuid:11111111-1111-4111-8111-111111111111"},
                "period": {"start": "2024-10-12", "end": "2024-10-17"},
            }},
            {"fullUrl": "urn:uuid:33333333-3333-4333-8333-333333333333", "resource": {
                "resourceType": "Observation", "id": "obs-1",
                "status": "final", "code": {"text": "Blood Pressure"},
                "subject": {"reference": "urn:uuid:11111111-1111-4111-8111-111111111111"},
                "encounter": {"reference": "urn:uuid:22222222-2222-4222-8222-222222222222"},
                "effectiveDateTime": "2024-10-12",
                "valueQuantity": {"value": 140, "unit": "mmHg"},
            }},
        ],
    }
    write(ROOT / "fhir_bundle" / "encounter.fhir.json", json.dumps(bundle, ensure_ascii=False))

    # 6. Filename-leak canary: the identifying value lives ONLY in the filename.
    write(ROOT / "leak_canary" / "SYNTHETIC_PHASE2_CANARY_8877665544.csv",
          "Patient Name,UHID\nAsha Rao,CANUH1\n")


if __name__ == "__main__":
    generate()
    print(f"wrote Phase 2 dev fixtures to {ROOT}")
