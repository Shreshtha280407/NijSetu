#!/usr/bin/env python3
"""Generate an INDEPENDENT synthetic corpus, plus its own answer key.

Why this exists. Every detection rule in this repository was developed while
measuring against the organizer's practice corpus, and a rule tuned on one
corpus can score perfectly on it while recognising nothing else. The only
honest way to find out which it is, is to measure on data that shares none of
the surface forms that were tuned against. So nothing here is drawn from the
practice data:

  * names, villages, hospitals and doctors are invented and asserted (see
    tests/test_heldout_generalization.py) not to occur in it;
  * identifier prefixes are new (KGH/NVH/MCN/SDC, never JJH/SNJ/DHH/ADM/IP);
  * header spellings are new ("Patient's Full Name", "Contact#",
    "Gram/Mohalla", "Reg. No.", "UHID No.");
  * file encodings include cp1252 and utf-16;
  * dates appear day-first, month-first and ISO, including the ambiguous
    region where only the scope convention decides;
  * the PII channels the organizer scores separately -- content, filename,
    JSON key, CSV header, wrapper metadata, base64 attachment -- each carry at
    least one entity.

The output is deliberately NOT committed and NOT used to tune anything: it is
regenerated from this script, which is the artifact under review.

    python scripts/make_heldout_corpus.py <output_dir>

Writes <output_dir>/input/** and <output_dir>/truth.json, where truth.json
lists every entity that must not survive and every clinical value that must.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

# --- invented population ----------------------------------------------------
# Deliberately NOT Indian-census-frequency-weighted: a de-identifier that only
# works on common names is not a de-identifier.
PEOPLE = [
    # (given, family, देवनागरी given, देवनागरी family, relation, relative, village, देवनागरी village, district)
    ("Jhilmil", "Barnwal", "झिलमिल", "बरनवाल", "W/o", "Trilok Barnwal", "Kusumpur", "कुसुमपुर", "Unnao"),
    ("Devyani", "Mahto", "देवयानी", "महतो", "D/o", "Shambhu Mahto", "Rasoolpur Kalan", "रसूलपुर कलां", "Sitapur"),
    ("Firoz", "Ansari", "फ़िरोज़", "अंसारी", "S/o", "Mehboob Ansari", "Chanddih", "चांडीह", "Sultanpur"),
    ("Yashoda", "Nigam", "यशोदा", "निगम", "W/o", "Hariom Nigam", "Baghaura Khurd", "बघौरा खुर्द", "Hardoi"),
    ("Tejpal", "Kushwaha", "तेजपाल", "कुशवाहा", "S/o", "Ramdeen Kushwaha", "Tikuriya Buzurg", "टिकुरिया बुज़ुर्ग", "Barabanki"),
    ("Noorjahan", "Qureshi", "नूरजहाँ", "क़ुरैशी", "W/o", "Shabbir Qureshi", "Mirzapur Buzurg", "मिर्ज़ापुर बुज़ुर्ग", "Rae Bareli"),
]
HOSPITALS = [
    ("Kusum Deep Charitable Hospital", "KGH", "kusumdeep"),
    ("Navjeevan Seva Sadan", "NVH", "navjeevan"),
    ("Matri Chhaya Nursing Home", "MCN", "matrichhaya"),
]
DOCTORS = ["Dr. Harjeet Bedi", "Dr. Sulekha Rastogi", "Dr. Imtiaz Baig"]
PHONES = ["7411920563", "9418827744", "8600113377", "7019442288", "9930551166", "6303778899"]
AADHAAR = ["2719 4458 1120", "9074 3318 5562", "5518 7729 0043",
           "3340 6612 8891", "7761 2204 3358", "8893 5516 7742"]
ABHA = ["12-3456-7890-1234", "21-6543-0987-4321", "33-1122-3344-5566",
        "44-9988-7766-5544", "55-1357-2468-9900", "66-2468-1357-0099"]
PANS = ["ABCPJ1234K", "BXZPD5678L", "CMNPF9012M", "DQRPY3456N", "EFGPT7890P", "HJKPN2345Q"]
PINS = ["209618", "261013", "228371", "241022", "225047", "229085"]

# Clinical values that must SURVIVE. Mixed units and shapes on purpose.
CLINICAL = [
    ("BP", "138/86"), ("pulse", "92/min"), ("Hb", "10.4 g/dL"),
    ("RBS", "212 mg/dL"), ("SpO2", "94%"), ("creatinine", "1.3 mg/dL"),
    ("HbA1c", "8.1%"), ("temp", "100.4 F"),
]
DIAGNOSES = [
    "Type 2 diabetes mellitus without complications",
    "Essential hypertension",
    "Iron deficiency anaemia",
    "Acute gastroenteritis",
    "Community acquired pneumonia",
    "Chronic kidney disease stage 3",
]
DRUGS = [
    "Tab Metformin 500mg 1-0-1 x 1 month",
    "Tab Telmisartan 40mg 1-0-0 x 2 wks",
    "Cap Doxycycline 100 mg 1-0-1 x 5 days",
    "Inj Ceftriaxone 1g IV x 7 days",
    "Tab Pantoprazole 40 mg 1-0-0 x 5 days",
    "Syp Lactulose 15 ml HS x 2 wks",
]


def _entity(surface: str, kind: str, script: str, channel: str) -> dict:
    return {"surface": surface, "type": kind, "script": script, "channel": channel}


def build(out: Path) -> dict:
    inp = out / "input"
    files: dict[str, dict] = {}

    def add(rel: str, data: bytes, units: dict) -> None:
        path = inp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files[rel] = {"units": units}

    # 1. Registration CSV with UNSEEN header spellings, cp1252-encoded.
    header = ("Reg. No.,Patient's Full Name,Sex,Age,Birth Date (dd-mm-yyyy),Relation,"
              "Father's/Husband's Name,Residential Address,Gram/Mohalla,Tehsil,"
              "District,Pin Code,Contact#,Aadhaar Number,ABHA Address,PAN\n")
    rows, units = [header], {}
    for i, (g, f, _dg, _df, rel, relative, vill, _dv, dist) in enumerate(PEOPLE):
        uhid = f"{HOSPITALS[i % 3][1]}-00{71000 + i * 37}"
        addr = f"Makan No. {603 + i * 7}, near Tikuriya Pokhra, Mohalla Barnwal Tola"
        rows.append(
            f"{uhid},{g} {f},{'F' if rel == 'W/o' or rel == 'D/o' else 'M'},{28 + i * 6},"
            f"{(i % 28) + 1:02d}-{(i % 12) + 1:02d}-{1968 + i * 3},{rel},{relative},"
            f'"{addr}",{vill},Hasanganj,{dist},{PINS[i]},{PHONES[i]},{AADHAAR[i]},'
            f"{g.lower()}.{f.lower()}@abdm,{PANS[i]}\n"
        )
        units[f"row:{i + 1}"] = {
            "link": "required", "patient": f"HP{i:04d}",
            "entities": [
                _entity(uhid, "MRN", "latin", "content"),
                _entity(f"{g} {f}", "PATIENT_NAME", "latin", "content"),
                _entity(relative, "RELATIVE_NAME", "latin", "content"),
                _entity(vill, "VILLAGE", "latin", "content"),
                _entity(PHONES[i], "PHONE", "latin", "content"),
                _entity(AADHAAR[i], "AADHAAR", "latin", "content"),
                _entity(PINS[i], "PIN", "latin", "content"),
                _entity(PANS[i], "PAN", "latin", "content"),
                _entity(f"Makan No. {603 + i * 7}", "HOUSE", "latin", "content"),
                _entity("near Tikuriya Pokhra", "LANDMARK", "latin", "content"),
                _entity("Mohalla Barnwal Tola", "LOCALITY", "latin", "content"),
            ],
            "preserve": [],
        }
    add("registration/kusumdeep_register.csv", "".join(rows).encode("cp1252"), units)

    # 2. Hinglish-header TSV, utf-16 encoded.
    tsv = ["Parchi Sankhya\tMarij ka Poora Naam\tUmar\tJanm Tithi\tPita/Pati ka Naam\tPata\tGaon\tJila\tMobile No\n"]
    units = {}
    for i, (g, f, _dg, _df, rel, relative, vill, _dv, dist) in enumerate(PEOPLE[:3]):
        pid = f"NVH/27/{40011 + i * 13}"
        tsv.append(f"{pid}\t{g} {f}\t{29 + i}\t{(i % 28) + 1:02d}/{(i % 12) + 1:02d}/1979\t"
                   f"{rel} {relative}\tMakan No. {90 + i}\t{vill}\t{dist}\t{PHONES[i]}\n")
        units[f"row:{i + 1}"] = {
            "link": "required", "patient": f"HP{i:04d}",
            "entities": [
                _entity(pid, "MRN", "latin", "content"),
                _entity(f"{g} {f}", "PATIENT_NAME", "hinglish", "content"),
                _entity(relative, "RELATIVE_NAME", "hinglish", "content"),
                _entity(vill, "VILLAGE", "hinglish", "content"),
                _entity(PHONES[i], "PHONE", "latin", "content"),
            ],
            "preserve": [],
        }
    add("registration/navjeevan_parchi.tsv", "".join(tsv).encode("utf-16"), units)

    # 3. Devanagari OPD slips -- name in the label AND in the sentence body.
    for i, (g, f, dg, df, _rel, _relative, _vill, dv, _dist) in enumerate(PEOPLE):
        hosp, prefix, slug = HOSPITALS[i % 3]
        text = (
            f"{hosp} - ओपीडी पर्चा\n"
            f"नाम: {dg} {df}   आयु: {31 + i} वर्ष\n"
            f"ग्राम: {dv}\n"
            f"तारीख: {(i % 28) + 1:02d}-{(i % 12) + 1:02d}-2027\n"
            f"आईपीडी सं.: {prefix}-IPD-00{412 + i}\n"
            f"{dg} {df} को {4 + i} दिन से बुखार है। {DIAGNOSES[i]}.\n"
            f"Rx\n1. {DRUGS[i]}\n"
            f"{CLINICAL[i][0]} {CLINICAL[i][1]}\n"
            f"{DOCTORS[i % 3]} (MD Medicine)\n"
        )
        add(f"prescriptions/rx_{slug}_{i:05d}.txt", text.encode("utf-8"), {
            "doc": {
                "link": "required", "patient": f"HP{i:04d}",
                "entities": [
                    _entity(f"{dg} {df}", "PATIENT_NAME", "deva", "content"),
                    _entity(dv, "VILLAGE", "deva", "content"),
                    _entity(f"{prefix}-IPD-00{412 + i}", "MRN", "latin", "content"),
                ],
                "preserve": [{"text": DRUGS[i]}, {"text": CLINICAL[i][1]}, {"text": DIAGNOSES[i]}],
            },
        })

    # 4. JSON with an identifying FILENAME, identifying KEYS, wrapper METADATA,
    #    an unlabelled episode id in a banner, and a base64 attachment.
    for i, (g, f, _dg, _df, rel, relative, vill, _dv, dist) in enumerate(PEOPLE[:4]):
        hosp, prefix, slug = HOSPITALS[i % 3]
        uhid = f"{prefix}-00{71000 + i * 37}"
        attachment = (
            f"DISCHARGE SUMMARY\nPatient: {g} {f}\nUHID: {uhid}\n"
            f"Mobile: {PHONES[i]}\nDiagnosis: {DIAGNOSES[i]}\n"
        )
        payload = {
            "_export_meta": {
                "system": "eHospital OPD module",
                "hospital": hosp,
                "source_path": f"D:\\\\HIS\\\\exports\\\\opd\\\\{g}_{f}_{71000 + i * 37}\\\\visits.json",
            },
            # The UHID used as a JSON KEY, which is its own scored channel.
            "patients": {
                uhid: {
                    "hospital_uhid": uhid,
                    "patient_full_name": f"{g} {f}",
                    "primary_contact_number": PHONES[i],
                    "abha_number": ABHA[i],
                    "banner": f"NURSING NOTES - Ward {37 + i} (Medicine), Bed {52 + i} - {prefix}-IPD-00{412 + i}",
                    "remarks": f"Sample received from {g}'s attendant ({rel} {relative}).",
                    "note": f"{g} ka {CLINICAL[i][0]} {CLINICAL[i][1]}, {DIAGNOSES[i]}.",
                    "attachment_b64": base64.b64encode(attachment.encode("utf-8")).decode("ascii"),
                    "visit_date": f"2027-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                },
            },
        }
        rel_path = f"opd/{slug}_{g}_{f}_{uhid}.json"
        add(rel_path, json.dumps(payload, indent=2).encode("utf-8"), {
            "doc": {
                "link": "required", "patient": f"HP{i:04d}",
                "entities": [
                    _entity(uhid, "MRN", "latin", "key"),
                    _entity(f"{g} {f}", "PATIENT_NAME", "latin", "content"),
                    _entity(g, "PATIENT_NAME", "hinglish", "content"),
                    _entity(PHONES[i], "PHONE", "latin", "content"),
                    _entity(ABHA[i], "ABHA_NUMBER", "latin", "content"),
                    _entity(relative, "RELATIVE_NAME", "latin", "content"),
                    _entity(f"{prefix}-IPD-00{412 + i}", "MRN", "latin", "content"),
                    _entity(f"{g}_{f}_{71000 + i * 37}", "PATIENT_NAME", "latin", "metadata"),
                    _entity(uhid, "MRN", "latin", "filename"),
                    _entity(f"{g}_{f}", "PATIENT_NAME", "latin", "filename"),
                    # inside the base64 payload
                    _entity(attachment.split("Patient: ")[1].split("\n")[0], "PATIENT_NAME", "latin", "base64"),
                ],
                "preserve": [{"text": CLINICAL[i][1]}, {"text": DIAGNOSES[i]}],
            },
        })

    # 5. Lab CSV whose HEADER row carries patient identifiers.
    head = ("time," + ",".join(f"{HOSPITALS[i % 3][1]}-00{71000 + i * 37} {PEOPLE[i][0]}"
                               for i in range(3)) + ",patient_ref\n")
    body = "".join(f"{8 + r}:00," + ",".join(CLINICAL[(r + c) % len(CLINICAL)][1] for c in range(3))
                   + f",{HOSPITALS[0][1]}-00{71000}\n" for r in range(3))
    add("misc/ward_vitals.csv", (head + body).encode("utf-8"), {
        "row:1": {"entities": [
            _entity(f"{HOSPITALS[i % 3][1]}-00{71000 + i * 37}", "MRN", "latin", "header") for i in range(3)
        ] + [_entity(PEOPLE[i][0], "PATIENT_NAME", "latin", "header") for i in range(3)], "preserve": []},
    })

    # 6. Latin free-text discharge summaries: unlabelled village, ward in an
    #    address, a possessive attendant, and an episode id in the filename.
    for i, (g, f, _dg, _df, rel, relative, vill, _dv, dist) in enumerate(PEOPLE[:3]):
        hosp, prefix, slug = HOSPITALS[i % 3]
        episode = f"{prefix}-IPD-00{412 + i}"
        text = (
            f"{hosp}\nDISCHARGE SUMMARY - {episode}\n"
            f"Patient: {g} {f}   Age: {31 + i}\n"
            f"Address: Makan No. {40 + i}, Ward {6 + i}, Mohalla Shanti Nagar, "
            f"{vill}, Tehsil Hasanganj, Distt. {dist} - {PINS[i]}\n"
            f"Contact: {PHONES[i]}\n"
            f"Diagnosis: {DIAGNOSES[i]}\n"
            f"{CLINICAL[i][0]}: {CLINICAL[i][1]}\n"
            f"Rx: {DRUGS[i]}\n"
            f"Discharged in care of {g}'s attendant ({rel} {relative}).\n"
            f"{DOCTORS[i % 3]}\n"
        )
        add(f"discharge/{slug}_ds_{episode}.txt", text.encode("utf-8"), {
            "doc": {
                "link": "required", "patient": f"HP{i:04d}",
                "entities": [
                    _entity(episode, "MRN", "latin", "content"),
                    _entity(episode, "MRN", "latin", "filename"),
                    _entity(f"{g} {f}", "PATIENT_NAME", "latin", "content"),
                    _entity(relative, "RELATIVE_NAME", "latin", "content"),
                    _entity(vill, "VILLAGE", "latin", "content"),
                    _entity(f"Ward {41 + i}", "LOCALITY", "latin", "content"),
                    _entity(f"Makan No. {902 + i}", "HOUSE", "latin", "content"),
                    _entity(PHONES[i], "PHONE", "latin", "content"),
                    _entity(PINS[i], "PIN", "latin", "content"),
                ],
                "preserve": [{"text": DIAGNOSES[i]}, {"text": CLINICAL[i][1]}, {"text": DRUGS[i]}],
            },
        })

    truth = {"files": files}
    (out / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    return truth


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    truth = build(out)
    entities = sum(len(u["entities"]) for f in truth["files"].values() for u in f["units"].values())
    preserved = sum(len(u.get("preserve") or []) for f in truth["files"].values() for u in f["units"].values())
    print(f"wrote {len(truth['files'])} files, {entities} entities, {preserved} clinical values to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
