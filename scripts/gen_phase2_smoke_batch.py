#!/usr/bin/env python3
"""Deterministic synthetic ~1,000-record Phase 2 smoke workload.

Same seeded distribution as Phase 1's gen_smoke_batch.py (mixed CSV/JSON/TXT,
duplicate patients across formats, multiple encounters per patient), extended
with admission/discharge dates so the date-shift + clinical-interval path is
exercised at scale, plus a small FHIR bundle. A team workload sanity check,
not a judge resource guarantee.
"""

from __future__ import annotations

import datetime
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nijsetu.gazetteer import first_names, surnames  # noqa: E402

SEED = 20261125


def generate(out_dir: Path, n_patients: int = 300, seed: int = SEED) -> None:
    rng = random.Random(seed)
    firsts = sorted(first_names())
    lasts = sorted(surnames())
    out_dir.mkdir(parents=True, exist_ok=True)

    reg_rows = ["Patient Name,UHID,Phone,Address,Admission Date,Discharge Date"]
    consultations = []
    notes_dir = out_dir / "notes"
    notes_dir.mkdir(exist_ok=True)
    base_date = datetime.date(2024, 1, 1)

    for i in range(n_patients):
        first = rng.choice(firsts).title()
        last = rng.choice(lasts).title()
        name = f"{first} {last}"
        uhid = f"UH{i:05d}"
        phone = f"9{rng.randint(100000000, 999999999)}"
        ward = rng.randint(1, 20)
        admission = base_date + datetime.timedelta(days=rng.randint(0, 300))
        discharge = admission + datetime.timedelta(days=rng.randint(1, 10))
        reg_rows.append(
            f'{name},{uhid},{phone},"C/O Family, Ward {ward}",{admission.isoformat()},{discharge.isoformat()}'
        )

        n_encounters = 2 if i % 4 == 0 else 1
        for enc in range(n_encounters):
            hba1c = round(rng.uniform(5.5, 10.5), 1)
            consultations.append({"patientName": name, "uhid": uhid, "encounter": enc, "hba1c": f"{hba1c}%"})
            bp_sys = rng.randint(100, 160)
            bp_dia = rng.randint(70, 100)
            note_text = (
                f"OPD note {i}-{enc}\nPatient: {name}, UHID {uhid}\n"
                f"Admission Date: {admission.isoformat()}\nDischarge Date: {discharge.isoformat()}\n"
                f"BP {bp_sys}/{bp_dia}. K/C/O hypertension. Ramipril 5 mg once daily.\n"
            )
            (notes_dir / f"note_{i:05d}_{enc}.txt").write_text(note_text, encoding="utf-8")

    (out_dir / "registration.csv").write_text("\n".join(reg_rows) + "\n", encoding="utf-8")
    (out_dir / "consultations.json").write_text(json.dumps(consultations, ensure_ascii=False), encoding="utf-8")

    bundle = {
        "resourceType": "Bundle", "type": "collection",
        "entry": [
            {"fullUrl": "urn:uuid:11111111-1111-4111-8111-111111111111", "resource": {
                "resourceType": "Patient", "id": "smoke-pat-1",
                "identifier": [{"system": "urn:local:uhid", "value": "SMOKEFHIR1"}],
                "name": [{"text": "Fhir Smoketest"}],
                "address": [{"district": "Pune", "postalCode": "411001"}],
            }},
            {"fullUrl": "urn:uuid:22222222-2222-4222-8222-222222222222", "resource": {
                "resourceType": "Observation", "id": "smoke-obs-1", "status": "final",
                "code": {"text": "Blood Pressure"},
                "subject": {"reference": "urn:uuid:11111111-1111-4111-8111-111111111111"},
                "effectiveDateTime": "2024-10-12",
                "valueQuantity": {"value": 132, "unit": "mmHg"},
            }},
        ],
    }
    (out_dir / "fhir_smoke.fhir.json").write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/nijsetu_phase2_smoke_batch")
    generate(target)
    print(f"wrote Phase 2 smoke batch to {target}")
