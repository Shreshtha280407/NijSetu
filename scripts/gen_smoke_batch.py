#!/usr/bin/env python3
"""Deterministic synthetic 1,000-record Phase 1 smoke batch generator.

Seeded RNG over bundled gazetteer names -- mixed CSV/JSON/TXT/FHIR, known
duplicate patients across formats, multiple encounters per patient. A team
workload sanity check, not a judge resource guarantee (see blueprint page 19).
"""

from __future__ import annotations

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

    reg_rows = ["Patient Name,UHID,Phone,Address"]
    consultations = []
    notes_dir = out_dir / "notes"
    notes_dir.mkdir(exist_ok=True)

    for i in range(n_patients):
        first = rng.choice(firsts).title()
        last = rng.choice(lasts).title()
        name = f"{first} {last}"
        uhid = f"UH{i:05d}"
        phone = f"9{rng.randint(100000000, 999999999)}"
        ward = rng.randint(1, 20)
        reg_rows.append(f'{name},{uhid},{phone},"C/O Family, Ward {ward}"')

        # roughly 1 in 4 patients get a second encounter (duplicate across formats)
        n_encounters = 2 if i % 4 == 0 else 1
        for enc in range(n_encounters):
            consultations.append({"patientName": name, "uhid": uhid, "encounter": enc})
            bp_sys = rng.randint(100, 160)
            bp_dia = rng.randint(70, 100)
            note_text = (
                f"OPD note {i}-{enc}\nPatient: {name}, UHID {uhid}\n"
                f"BP {bp_sys}/{bp_dia}. K/C/O hypertension. Ramipril 5 mg once daily.\n"
            )
            (notes_dir / f"note_{i:05d}_{enc}.txt").write_text(note_text, encoding="utf-8")

    (out_dir / "registration.csv").write_text("\n".join(reg_rows) + "\n", encoding="utf-8")
    (out_dir / "consultations.json").write_text(json.dumps(consultations, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/nijsetu_smoke_batch")
    generate(target)
    print(f"wrote smoke batch to {target}")
