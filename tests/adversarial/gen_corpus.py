#!/usr/bin/env python3
"""Independent adversarial corpus generator for NijSetu (stdlib only).

Builds synthetic hospital-export data across seven stress families (novel
hospital formats, multilingual chaos, hidden PII channels, clinical-meaning
traps, difficult identity linkage, a re-identification decoy registry, and
corrupt/unsupported files), plus a ground-truth file kept OUTSIDE the input
directory NijSetu is run against.

This generator is independent of NijSetu's own detectors: it is driven by a
fixed cast of names/places/formats written here, not by NijSetu's gazetteer
or regexes, so passing it is not circular. It is NOT independent of the
person who wrote it (a single author, one style of "unseen" data) -- see
the "independence limitation" note printed by `--manifest`.

Usage:
    python3 gen_corpus.py --split dev        --seed 1001 --out corpora/dev
    python3 gen_corpus.py --split validation --seed 2002 --out corpora/validation
    python3 gen_corpus.py --split blind      --seed 3003 --out corpora/blind
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import random
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

# ----------------------------------------------------------------------------
# Name / place cast -- all synthetic, invented for this generator, never
# copied from the organizer's practice corpus or answer key.
# ----------------------------------------------------------------------------

LATIN_FIRST = ["Tarun", "Meenal", "Avik", "Pallavi", "Rustam", "Shabnam", "Ovais", "Deepti",
               "Nikhat", "Baldev", "Sarika", "Yogendra", "Farida", "Harleen", "Suraj", "Komal"]
LATIN_LAST = ["Bhagat", "Saluja", "Rizvi", "Khandare", "Toppo", "Sahani", "Bedi", "Chauhan",
              "Warsi", "Deka", "Nair", "Kalita"]
DEVA_FIRST = ["तरुण", "मीनल", "अविक", "पल्लवी", "रुस्तम", "शबनम", "औवैस", "दीप्ति",
              "निकहत", "बलदेव", "सारिका", "योगेंद्र"]
DEVA_LAST = ["भगत", "सलुजा", "रिज़वी", "खंडारे", "टोप्पो", "साहनी", "बेदी", "चौहान"]
VILLAGES = ["Rampur Khera", "Narhi", "Bishunpur", "Madhavganj", "Kotra Sultanpur", "Dhanauli",
            "Itwa Bazar", "Semra", "Gokulpur", "Sarai Mir"]
TEHSILS = ["Nawabganj", "Kadipur", "Lambhua", "Musafirkhana", "Haidergarh"]
DISTRICTS = ["Sultanpur", "Barabanki", "Amethi", "Pratapgarh"]
NEW_HOSPITALS = ["Arogya Nilayam", "Shubh Shanti Hospital", "Vatsalya Mother & Child Care",
                 "Grameen Swasthya Kendra"]
DOCTORS = ["Dr. Imtiaz Alam", "Dr. Supriya Hegde", "Dr. Jagmohan Rawat", "Dr. Felicia D'Souza"]
ICD_POOL = [("E11.9", "Type 2 diabetes mellitus without complications"),
            ("I10", "Essential (primary) hypertension"),
            ("J45.909", "Unspecified asthma, uncomplicated"),
            ("N18.3", "Chronic kidney disease, stage 3"),
            ("A09", "Infectious gastroenteritis and colitis, unspecified")]
MEDS = [("Metformin", "500mg", "1-0-1"), ("Amlodipine", "5mg", "1-0-0"),
        ("Salbutamol inhaler", "100mcg", "0-0-2 PRN"), ("Azithromycin", "500mg", "1-0-0 x3d")]
LAB_TESTS = [("HbA1c", "7.8", "%"), ("Serum Creatinine", "1.3", "mg/dL"),
             ("Random Blood Sugar", "212", "mg/dL"), ("Haemoglobin", "10.9", "g/dL")]


@dataclass
class Patient:
    pid: str
    name_latin: str
    name_deva: str | None
    dob: date
    sex: str
    village: str
    tehsil: str
    district: str
    pin: str
    phone: str
    aadhaar: str
    relative_name: str
    doctor: str
    diagnosis: tuple
    medication: tuple
    lab: tuple
    admission: date
    discharge: date
    local_ids: dict = field(default_factory=dict)


def _aadhaar(rng: random.Random) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(12))


def _phone(rng: random.Random) -> str:
    return "9" + "".join(str(rng.randint(0, 9)) for _ in range(9))


def _pin(rng: random.Random, district_idx: int) -> str:
    return f"22{district_idx}{rng.randint(100, 999)}"


def make_patient(rng: random.Random, pid: str, *, dob: date | None = None, name_latin: str | None = None,
                  relative_name: str | None = None) -> Patient:
    fi, la = rng.choice(LATIN_FIRST), rng.choice(LATIN_LAST)
    name_latin = name_latin or f"{fi} {la}"
    try:
        idx = LATIN_FIRST.index(fi)
        name_deva = f"{DEVA_FIRST[idx % len(DEVA_FIRST)]} {rng.choice(DEVA_LAST)}"
    except ValueError:
        name_deva = None
    dob = dob or date(rng.randint(1945, 2024), rng.randint(1, 12), rng.randint(1, 28))
    admission = date(2026, rng.randint(1, 11), rng.randint(1, 27))
    discharge = admission + timedelta(days=rng.randint(1, 9))
    d_idx = rng.randrange(len(DISTRICTS))
    return Patient(
        pid=pid, name_latin=name_latin, name_deva=name_deva, dob=dob,
        sex=rng.choice(["M", "F"]), village=rng.choice(VILLAGES), tehsil=rng.choice(TEHSILS),
        district=DISTRICTS[d_idx], pin=_pin(rng, d_idx), phone=_phone(rng), aadhaar=_aadhaar(rng),
        relative_name=relative_name or f"{rng.choice(LATIN_FIRST)} {la}", doctor=rng.choice(DOCTORS),
        diagnosis=rng.choice(ICD_POOL), medication=rng.choice(MEDS), lab=rng.choice(LAB_TESTS),
        admission=admission, discharge=discharge,
    )


def dmy(d: date) -> str:
    return d.strftime("%d-%m-%Y")


# ----------------------------------------------------------------------------
# Truth accumulator
# ----------------------------------------------------------------------------

class Truth:
    def __init__(self, seed: int, split: str):
        self.seed = seed
        self.split = split
        self.files: dict[str, dict] = {}
        self.canaries: list[dict] = []
        self.registry: list[dict] = []   # Family F decoy population
        self.known_facts: list[dict] = []

    def file(self, relpath: str, fmt: str) -> dict:
        return self.files.setdefault(relpath, {"format": fmt, "units": {}, "expect_quarantine": False})

    def unit(self, relpath: str, fmt: str, loc: str) -> dict:
        return self.file(relpath, fmt)["units"].setdefault(
            loc, {"entities": [], "preserve": [], "patient": None, "link": None})

    def entity(self, relpath, fmt, loc, *, type_, surface, script, channel):
        self.unit(relpath, fmt, loc)["entities"].append(
            {"type": type_, "surface": surface, "script": script, "channel": channel})

    def preserve(self, relpath, fmt, loc, text):
        self.unit(relpath, fmt, loc)["preserve"].append({"text": text})

    def link(self, relpath, fmt, loc, patient_cluster_id, required=True):
        u = self.unit(relpath, fmt, loc)
        u["patient"] = patient_cluster_id
        u["link"] = "required" if required else None

    def dump(self) -> dict:
        return {
            "schema": "nijsetu-adversarial-truth-v1", "seed": self.seed, "split": self.split,
            "files": self.files, "canaries": self.canaries, "registry": self.registry,
            "known_facts": self.known_facts,
        }


def script_of(s: str) -> str:
    has_deva = any("ऀ" <= c <= "ॿ" for c in s)
    has_latin = any(c.isascii() and c.isalpha() for c in s)
    if has_deva and has_latin:
        return "hinglish"
    return "deva" if has_deva else "latin"


# ----------------------------------------------------------------------------
# Family A -- new hospital systems: novel columns, legacy/odd formats,
# nested JSON, missing fields, column reordering, mixed date formats.
# ----------------------------------------------------------------------------

def family_a(rng: random.Random, out: Path, truth: Truth, n: int):
    rel = "registration_new_format/arogya_intake.csv"
    rows = [["Beneficiary", "Reg. Patient", "Patient Identity", "UHID/Case", "Sex@Birth",
              "DateOfBirth(DD.MM.YYYY)", "Village/Gaon", "Contact#", "Notes"]]
    for i in range(n):
        p = make_patient(rng, f"A-{i:03d}")
        uhid = f"ARG/{rng.randint(10,99)}/{rng.randint(1000,9999)}"
        dob_txt = p.dob.strftime("%d.%m.%Y")
        row = ["SELF", p.name_latin, p.name_latin, uhid, p.sex, dob_txt, p.village, p.phone,
               f"seen by {p.doctor}"]
        rows.append(row)
        loc = f"row:{i}"
        truth.entity(rel, "csv", loc, type_="PATIENT_NAME", surface=p.name_latin, script="latin", channel="content")
        truth.entity(rel, "csv", loc, type_="MRN", surface=uhid, script="latin", channel="content")
        truth.entity(rel, "csv", loc, type_="DOB", surface=dob_txt, script="latin", channel="content")
        truth.entity(rel, "csv", loc, type_="VILLAGE", surface=p.village, script="latin", channel="content")
        truth.entity(rel, "csv", loc, type_="PHONE", surface=p.phone, script="latin", channel="content")
        truth.preserve(rel, "csv", loc, p.doctor)
    _write_csv(out / rel, rows)

    # Legacy pipe-delimited export with reordered + duplicate-labelled columns.
    rel2 = "registration_new_format/legacy_export.txt"
    lines = ["ID|ID|NAME|NAME|DOB_YYYYMMDD|VILLAGE"]
    for i in range(max(3, n // 2)):
        p = make_patient(rng, f"A2-{i:03d}")
        legacy_id = f"LEG{rng.randint(100000,999999)}"
        dob_txt = p.dob.strftime("%Y%m%d")
        lines.append(f"{legacy_id}|{legacy_id}|{p.name_latin}|{p.name_latin}|{dob_txt}|{p.village}")
        loc = f"line{i+1}"
        truth.entity(rel2, "txt", loc, type_="MRN", surface=legacy_id, script="latin", channel="content")
        truth.entity(rel2, "txt", loc, type_="PATIENT_NAME", surface=p.name_latin, script="latin", channel="content")
        truth.entity(rel2, "txt", loc, type_="DOB", surface=dob_txt, script="latin", channel="content")
        truth.entity(rel2, "txt", loc, type_="VILLAGE", surface=p.village, script="latin", channel="content")
    (out / rel2).parent.mkdir(parents=True, exist_ok=True)
    (out / rel2).write_text("\n".join(lines), encoding="utf-8")

    # Nested JSON, optional/missing fields, inconsistent typing.
    rel3 = "registration_new_format/vatsalya_nested.json"
    records = []
    for i in range(max(3, n // 2)):
        p = make_patient(rng, f"A3-{i:03d}")
        rec = {
            "visit": {"patient": {"identity": {"full_name": p.name_latin, "dob": str(p.dob)},
                                   "contact": {"mobile": int(p.phone)} if i % 2 == 0 else {"mobile": p.phone}},
                      "facility": rng.choice(NEW_HOSPITALS)},
            "meta": {"village": p.village} if i % 3 else {},
        }
        records.append(rec)
        loc = f"/export_batch/{i}"
        truth.entity(rel3, "json", loc, type_="PATIENT_NAME", surface=p.name_latin, script="latin", channel="content")
        truth.entity(rel3, "json", loc, type_="DOB", surface=str(p.dob), script="latin", channel="content")
        truth.entity(rel3, "json", loc, type_="PHONE", surface=p.phone, script="latin", channel="content")
    (out / rel3).parent.mkdir(parents=True, exist_ok=True)
    (out / rel3).write_text(json.dumps({"export_batch": records}, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------------
# Family B -- multilingual chaos: Devanagari, Hinglish, zero-width chars,
# Indic digits, honorifics/patronymics, same-vs-different-person spelling.
# ----------------------------------------------------------------------------

ZWSP = "​"
ZWNJ = "‌"
INDIC_DIGITS = "०१२३४५६७८९"


def to_indic_digits(s: str) -> str:
    return "".join(INDIC_DIGITS[int(c)] if c.isdigit() else c for c in s)


def family_b(rng: random.Random, out: Path, truth: Truth, n: int):
    rel = "opd_multilingual/opd_notes.txt"
    lines = []
    for i in range(n):
        p = make_patient(rng, f"B-{i:03d}")
        variant = i % 4
        if variant == 0:  # pure Devanagari
            name = p.name_deva or p.name_latin
            script = "deva"
        elif variant == 1:  # Hinglish mixed sentence
            name = f"{p.name_latin.split()[0]} {rng.choice(DEVA_LAST)}"
            script = "hinglish"
        elif variant == 2:  # zero-width chars injected inside a Latin name (disguise attempt)
            first, last = p.name_latin.split()
            name = first[:2] + ZWSP + first[2:] + ZWNJ + " " + last
            script = "latin"
        else:  # Indic-digit DOB alongside Latin name
            name = p.name_latin
            script = "latin"
        dob_txt = to_indic_digits(dmy(p.dob)) if variant == 3 else dmy(p.dob)
        honorific = rng.choice(["Shri", "Smt.", "Ku."])
        line = f"{honorific} {name} Umar/Age {rng.randint(1,90)} DOB: {dob_txt} Gaon: {p.village}"
        lines.append(line)
        loc = f"line{i}"
        truth.entity(rel, "txt", loc, type_="PATIENT_NAME", surface=name, script=script_of(name) if script != "latin" else "latin", channel="content")
        truth.entity(rel, "txt", loc, type_="DOB", surface=p.dob.strftime("%d-%m-%Y"), script=script, channel="content")
        truth.entity(rel, "txt", loc, type_="VILLAGE", surface=p.village, script="latin", channel="content")
    (out / rel).parent.mkdir(parents=True, exist_ok=True)
    (out / rel).write_text("\n".join(lines), encoding="utf-8")

    # Homonym trap: two DIFFERENT real patients share an identical name; two
    # spellings of the SAME patient must still link. Ground truth records
    # cluster ids independent of surface spelling.
    rel2 = "opd_multilingual/homonym_and_alias.csv"
    rows = [["Naam", "Umar", "Gaon", "Mobile", "Reg No"]]
    shared_name = "Suman Kumari"
    homA = make_patient(rng, "B-HOM-A", name_latin=shared_name)
    homB = make_patient(rng, "B-HOM-B", name_latin=shared_name)
    rows.append([homA.name_latin, "41", homA.village, homA.phone, "HOM-001"])
    rows.append([homB.name_latin, "63", homB.village, homB.phone, "HOM-002"])
    alias = make_patient(rng, "B-ALIAS", name_latin="Rajendar Mishra")
    rows.append(["Rajendar Mishra", "55", alias.village, alias.phone, "ALS-001"])
    rows.append(["Rajender Mishraa", "55", alias.village, alias.phone, "ALS-002"])  # same person, misspelled + reissued id
    _write_csv(out / rel2, rows)
    truth.entity(rel2, "csv", "row:0", type_="PATIENT_NAME", surface=homA.name_latin, script="latin", channel="content")
    truth.entity(rel2, "csv", "row:1", type_="PATIENT_NAME", surface=homB.name_latin, script="latin", channel="content")
    truth.entity(rel2, "csv", "row:2", type_="PATIENT_NAME", surface="Rajendar Mishra", script="latin", channel="content")
    truth.entity(rel2, "csv", "row:3", type_="PATIENT_NAME", surface="Rajender Mishraa", script="latin", channel="content")
    truth.link(rel2, "csv", "row:0", "CLUSTER-HOM-A")
    truth.link(rel2, "csv", "row:1", "CLUSTER-HOM-B")  # must NOT merge with row0 despite identical name
    truth.link(rel2, "csv", "row:2", "CLUSTER-ALIAS")
    truth.link(rel2, "csv", "row:3", "CLUSTER-ALIAS")  # must merge with row2 despite misspelling


# ----------------------------------------------------------------------------
# Family C -- hidden PII channels: filenames, JSON keys, nested metadata,
# base64 payloads, URL-encoding, HTML entities.
# ----------------------------------------------------------------------------

def family_c(rng: random.Random, out: Path, truth: Truth, n: int):
    for i in range(n):
        p = make_patient(rng, f"C-{i:03d}")
        fname = f"lab_reports/report_{p.name_latin.replace(' ', '_')}_{p.aadhaar}.json"
        payload = {"patient_name": p.name_latin, "result": {"test": "CBC", "note": f"seen by {p.doctor}"}}
        b64_note = base64.b64encode(f"DOB {p.dob} phone {p.phone}".encode()).decode()
        payload["attachment_b64"] = b64_note
        payload["source_path_meta"] = f"/exports/{p.name_latin.replace(' ', '_')}/scan.pdf"
        (out / fname).parent.mkdir(parents=True, exist_ok=True)
        (out / fname).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        truth.entity(fname, "json", "@filename", type_="PATIENT_NAME", surface=p.name_latin, script="latin", channel="filename")
        truth.entity(fname, "json", "@filename", type_="AADHAAR", surface=p.aadhaar, script="latin", channel="filename")
        truth.entity(fname, "json", "/patient_name", type_="PATIENT_NAME", surface=p.name_latin, script="latin", channel="content")
        truth.entity(fname, "json", "/attachment_b64", type_="DOB", surface=str(p.dob), script="latin", channel="base64")
        truth.entity(fname, "json", "/attachment_b64", type_="PHONE", surface=p.phone, script="latin", channel="base64")
        truth.entity(fname, "json", "/source_path_meta", type_="PATIENT_NAME", surface=p.name_latin, script="latin", channel="metadata")
        truth.preserve(fname, "json", "/result/note", p.doctor)

    # URL-encoded + HTML-entity-encoded name inside a referral note.
    rel = "referrals/referral_log.txt"
    p = make_patient(rng, "C-ENC-000")
    url_enc = p.name_latin.replace(" ", "%20")
    html_enc = p.name_latin.replace(" ", "&nbsp;")
    text = f"Referral for {url_enc} (urlenc) and {html_enc} (htmlenc) to {rng.choice(NEW_HOSPITALS)}."
    (out / rel).parent.mkdir(parents=True, exist_ok=True)
    (out / rel).write_text(text, encoding="utf-8")
    truth.entity(rel, "txt", "line0", type_="PATIENT_NAME", surface=p.name_latin, script="latin", channel="content")


# ----------------------------------------------------------------------------
# Family D -- clinical meaning traps: content that MUST survive, phrased to
# look like PII (C/O, H/O, dose patterns, doctor-vs-relative, eponyms).
# ----------------------------------------------------------------------------

def family_d(rng: random.Random, out: Path, truth: Truth, n: int):
    rel = "prescriptions/rx_log.txt"
    lines = []
    for i in range(n):
        p = make_patient(rng, f"D-{i:03d}")
        med, dose, freq = p.medication
        icd, icd_text = p.diagnosis
        test, val, unit = p.lab
        block = (
            f"Name: {p.name_latin}   Age: {rng.randint(20,80)}/{p.sex}   Reg No: RX{rng.randint(10000,99999)}\n"
            f"C/O: fever x 3 days, H/O: {icd_text}\n"
            f"Dx: {icd} {icd_text}\n"
            f"Rx: {med} {dose} {freq}\n"
            f"{test}: {val} {unit}\n"
            f"Seen by {p.doctor}, Ward: {rng.choice(['Male Medical','Paediatric','ICU'])}\n"
            f"C/O {p.relative_name} (attendant)\n"
        )
        lines.append(block)
        loc = f"block{i}"
        truth.entity(rel, "txt", loc, type_="PATIENT_NAME", surface=p.name_latin, script="latin", channel="content")
        truth.entity(rel, "txt", loc, type_="RELATIVE_NAME", surface=p.relative_name, script="latin", channel="content")
        # Everything below is clinical content that must NOT be treated as PII.
        truth.preserve(rel, "txt", loc, f"{med} {dose} {freq}")
        truth.preserve(rel, "txt", loc, f"{test}: {val} {unit}")
        truth.preserve(rel, "txt", loc, f"{icd} {icd_text}")
        truth.preserve(rel, "txt", loc, p.doctor)
        truth.preserve(rel, "txt", loc, "C/O: fever x 3 days")
    (out / rel).parent.mkdir(parents=True, exist_ok=True)
    (out / rel).write_text("\n".join(lines), encoding="utf-8")

    # Eponym / clinical-term trap: a surname-shaped medical eponym must survive.
    rel2 = "discharge/discharge_summaries.txt"
    p = make_patient(rng, "D-EPO-000")
    text = (f"Patient {p.name_latin} assessed for Parkinson's disease and Down syndrome; "
            f"no Alzheimer's noted. Discharged on {dmy(p.discharge)}, admitted {dmy(p.admission)}.")
    (out / rel2).parent.mkdir(parents=True, exist_ok=True)
    (out / rel2).write_text(text, encoding="utf-8")
    truth.entity(rel2, "txt", "line0", type_="PATIENT_NAME", surface=p.name_latin, script="latin", channel="content")
    truth.preserve(rel2, "txt", "line0", "Parkinson's disease")
    truth.preserve(rel2, "txt", "line0", "Down syndrome")
    truth.preserve(rel2, "txt", "line0", "Alzheimer's")


# ----------------------------------------------------------------------------
# Family E -- difficult identity linkage: multi-ID same patient, cross-script
# same patient, twins, shared household phone, homonyms (shared with B),
# stale/missing phone, weak-vs-strong evidence.
# ----------------------------------------------------------------------------

def family_e(rng: random.Random, out: Path, truth: Truth, n: int):
    rel = "registration_new_format/multi_hospital_links.csv"
    rows = [["UHID", "Name", "DOB", "Phone", "Village"]]
    r = 0

    # Same real patient at two hospitals: different local ID, same DOB,
    # slightly different name spelling -- STRONG evidence, must link.
    base = make_patient(rng, "E-STRONG")
    rows.append([f"HOSP1-{rng.randint(1000,9999)}", base.name_latin, dmy(base.dob), base.phone, base.village])
    rows.append([f"HOSP2-{rng.randint(1000,9999)}", base.name_latin.replace("i", "ee", 1), dmy(base.dob), base.phone, base.village])
    truth.link(rel, "csv", f"row:{r}", "CLUSTER-E-STRONG"); r += 1
    truth.link(rel, "csv", f"row:{r}", "CLUSTER-E-STRONG"); r += 1

    # Twins: same surname, same DOB (common for twins), same household phone,
    # DIFFERENT first names -- must NOT merge.
    twin_last = rng.choice(LATIN_LAST)
    twin_dob = date(2024, 3, 14)
    twin_phone = _phone(rng)
    rows.append([f"TWIN-{rng.randint(100,999)}", f"Aarav {twin_last}", dmy(twin_dob), twin_phone, "Dhanauli"])
    rows.append([f"TWIN-{rng.randint(100,999)}", f"Vihaan {twin_last}", dmy(twin_dob), twin_phone, "Dhanauli"])
    truth.link(rel, "csv", f"row:{r}", "CLUSTER-E-TWIN-A"); r += 1
    truth.link(rel, "csv", f"row:{r}", "CLUSTER-E-TWIN-B"); r += 1

    # Parent + child, identical surname, shared phone, clearly different DOB
    # (30-year gap) -- must NOT merge.
    fam_last = rng.choice(LATIN_LAST)
    fam_phone = _phone(rng)
    parent_dob = date(1978, 6, 2)
    child_dob = date(2019, 9, 10)
    rows.append([f"FAM-{rng.randint(100,999)}", f"Harbans {fam_last}", dmy(parent_dob), fam_phone, "Narhi"])
    rows.append([f"FAM-{rng.randint(100,999)}", f"Kiara {fam_last}", dmy(child_dob), fam_phone, "Narhi"])
    truth.link(rel, "csv", f"row:{r}", "CLUSTER-E-PARENT"); r += 1
    truth.link(rel, "csv", f"row:{r}", "CLUSTER-E-CHILD"); r += 1

    # Weak evidence: same village + same decade-of-birth, DIFFERENT name and
    # phone -- must NOT be linked (abstention is correct here).
    w1 = make_patient(rng, "E-WEAK-1")
    w2 = make_patient(rng, "E-WEAK-2", dob=w1.dob + timedelta(days=200))
    w2.village = w1.village
    rows.append([f"WK-{rng.randint(100,999)}", w1.name_latin, dmy(w1.dob), w1.phone, w1.village])
    rows.append([f"WK-{rng.randint(100,999)}", w2.name_latin, dmy(w2.dob), w2.phone, w2.village])
    truth.link(rel, "csv", f"row:{r}", "CLUSTER-E-WEAK-1"); r += 1
    truth.link(rel, "csv", f"row:{r}", "CLUSTER-E-WEAK-2"); r += 1

    for i in range(2, len(rows)):
        name, dob_txt, phone, village = rows[i][1], rows[i][2], rows[i][3], rows[i][4]
        loc = f"row:{i-1}"
        truth.entity(rel, "csv", loc, type_="PATIENT_NAME", surface=name, script="latin", channel="content")
        truth.entity(rel, "csv", loc, type_="DOB", surface=dob_txt, script="latin", channel="content")
        truth.entity(rel, "csv", loc, type_="PHONE", surface=phone, script="latin", channel="content")
        truth.entity(rel, "csv", loc, type_="VILLAGE", surface=village, script="latin", channel="content")
    _write_csv(out / rel, rows)

    # Issuer-local-ID collision: the SAME local numeric id reused by two
    # DIFFERENT hospitals (two separate export files/issuers) for two
    # different, unrelated patients -- must NOT merge on ID alone.
    collide_id = "LOC-0042"
    u1 = make_patient(rng, "E-COLLIDE-1")
    u2 = make_patient(rng, "E-COLLIDE-2")
    rel_c1, rel_c2 = "registration_new_format/hospitalA_intake.csv", "registration_new_format/hospitalB_intake.csv"
    _write_csv(out / rel_c1, [["UHID", "Name", "DOB", "Phone", "Village"],
                              [collide_id, u1.name_latin, dmy(u1.dob), u1.phone, u1.village]])
    _write_csv(out / rel_c2, [["UHID", "Name", "DOB", "Phone", "Village"],
                              [collide_id, u2.name_latin, dmy(u2.dob), u2.phone, u2.village]])
    for rel_c, p in ((rel_c1, u1), (rel_c2, u2)):
        truth.entity(rel_c, "csv", "row:0", type_="PATIENT_NAME", surface=p.name_latin, script="latin", channel="content")
        truth.entity(rel_c, "csv", "row:0", type_="DOB", surface=dmy(p.dob), script="latin", channel="content")
        truth.entity(rel_c, "csv", "row:0", type_="PHONE", surface=p.phone, script="latin", channel="content")
        truth.entity(rel_c, "csv", "row:0", type_="VILLAGE", surface=p.village, script="latin", channel="content")
    truth.link(rel_c1, "csv", "row:0", "CLUSTER-E-COLLIDE-1")
    truth.link(rel_c2, "csv", "row:0", "CLUSTER-E-COLLIDE-2")


# ----------------------------------------------------------------------------
# Family F -- re-identification attack surface: a decoy population registry
# plus known-fact probes, in the SAME shape ATTACKS.md describes (A1-A5 local
# proxy only -- not the organizer's hidden attacker/registry/seed).
# ----------------------------------------------------------------------------

def family_f(rng: random.Random, out: Path, truth: Truth, n: int, patients_for_registry: list[Patient]):
    for p in patients_for_registry:
        truth.registry.append({
            "name": p.name_latin, "dob": str(p.dob), "village": p.village, "district": p.district,
            "sex": p.sex, "is_patient": True,
        })
    for i in range(n * 3):
        decoy = make_patient(rng, f"F-DECOY-{i:03d}")
        truth.registry.append({
            "name": decoy.name_latin, "dob": str(decoy.dob), "village": decoy.village,
            "district": decoy.district, "sex": decoy.sex, "is_patient": False,
        })
    for p in patients_for_registry[: max(1, n // 2)]:
        truth.known_facts.append({
            "fact": "visit_date", "patient_name": p.name_latin, "value": str(p.admission),
            "village": p.village, "sex": p.sex,
        })
        truth.known_facts.append({
            "fact": "length_of_stay_days", "patient_name": p.name_latin,
            "value": (p.discharge - p.admission).days,
        })


# ----------------------------------------------------------------------------
# Family G -- corrupt / unsupported files: must be quarantined or exported
# without a raw PII copy, never silently dropped, never crash the run.
# ----------------------------------------------------------------------------

def family_g(rng: random.Random, out: Path, truth: Truth):
    p = make_patient(rng, "G-CORRUPT-000")

    # Truncated CSV (header + one full row + one row cut mid-field) with PII.
    rel = "corrupt/truncated_patients.csv"
    content = f"name,phone,aadhaar\n{p.name_latin},{p.phone},{p.aadhaar}\nIncomplete Pers"
    (out / rel).parent.mkdir(parents=True, exist_ok=True)
    (out / rel).write_text(content, encoding="utf-8")
    truth.file(rel, "csv")["expect_quarantine"] = "either"
    truth.file(rel, "csv")["has_pii"] = True

    # Malformed JSON (unterminated) with PII.
    rel2 = "corrupt/malformed.json"
    (out / rel2).write_text(f'{{"patient_name": "{p.name_latin}", "phone": "{p.phone}"', encoding="utf-8")
    truth.file(rel2, "json")["expect_quarantine"] = "either"
    truth.file(rel2, "json")["has_pii"] = True

    # Broken FHIR Bundle: missing resourceType, PII inline.
    rel3 = "corrupt/broken_bundle.json"
    (out / rel3).write_text(json.dumps({"entry": [{"resource": {"name": [{"text": p.name_latin}]}}]}), encoding="utf-8")
    truth.file(rel3, "json")["expect_quarantine"] = "either"
    truth.file(rel3, "json")["has_pii"] = True

    # Unknown extension, text content, PII.
    rel4 = "corrupt/weird_extension.xyz"
    (out / rel4).write_text(f"Patient: {p.name_latin} Phone: {p.phone}", encoding="utf-8")
    truth.file(rel4, "txt")["expect_quarantine"] = "either"
    truth.file(rel4, "txt")["has_pii"] = True

    # UTF-16 LE with BOM, legit CSV-ish content, PII -- must not be misread as garbage.
    rel5 = "corrupt/utf16_patients.csv"
    text = f"name,phone\n{p.name_latin},{p.phone}\n"
    (out / rel5).write_bytes(text.encode("utf-16"))
    truth.file(rel5, "csv")["has_pii"] = True

    # Windows-1252 with a curly quote, PII.
    rel6 = "corrupt/cp1252_notes.txt"
    text6 = f"Patient’s name: {p.name_latin}, phone {p.phone}"
    (out / rel6).write_bytes(text6.encode("cp1252"))
    truth.file(rel6, "txt")["has_pii"] = True

    # Empty file.
    rel7 = "corrupt/empty.csv"
    (out / rel7).write_text("", encoding="utf-8")

    # Invalid byte sequence (not valid UTF-8 or any common encoding).
    rel8 = "corrupt/invalid_bytes.csv"
    (out / rel8).write_bytes(b"name,phone\n\xff\xfe\x00broken,123\n")

    # Duplicate JSON keys (same key repeated -- last one wins per json.loads,
    # but the file itself is adversarial input, not malformed per se).
    rel9 = "corrupt/dup_keys.json"
    (out / rel9).write_text(
        '{"patient_name": "' + p.name_latin + '", "patient_name": "Someone Else", "phone": "' + p.phone + '"}',
        encoding="utf-8")
    truth.file(rel9, "json")["has_pii"] = True

    # Deeply nested JSON (stress recursion / stack limits), no PII.
    rel10 = "corrupt/deep_nesting.json"
    node: dict = {"v": 1}
    for _ in range(300):
        node = {"child": node}
    (out / rel10).write_text(json.dumps(node), encoding="utf-8")


# ----------------------------------------------------------------------------
# Canaries
# ----------------------------------------------------------------------------

def plant_canaries(rng: random.Random, out: Path, truth: Truth):
    canaries = [
        ("Zyntherax Okonwo-Varma", "9999900001"),
        ("Qzylbrindt Oseivantu", "9999900002"),
    ]
    rel = "misc/canary_notes.txt"
    lines = []
    for name, phone in canaries:
        lines.append(f"Patient {name}, contact {phone}, routine checkup.")
        truth.canaries.append({"surface": name})
        truth.canaries.append({"surface": phone})
    (out / rel).parent.mkdir(parents=True, exist_ok=True)
    (out / rel).write_text("\n".join(lines), encoding="utf-8")


# ----------------------------------------------------------------------------
# Clean control documents (token-retention / over-redaction detector).
# ----------------------------------------------------------------------------

def clean_controls(rng: random.Random, out: Path, truth: Truth):
    rel = "misc/hospital_policy.txt"
    text = ("General ward visiting hours are 4pm to 6pm daily. Masks are mandatory in the "
            "ICU and NICU. Billing counter closes at 5pm. Pharmacy stock of Metformin and "
            "Amlodipine is replenished every Monday. No patient data is referenced here.")
    (out / rel).parent.mkdir(parents=True, exist_ok=True)
    (out / rel).write_text(text, encoding="utf-8")
    truth.file(rel, "txt")["is_clean_control"] = True
    truth.preserve(rel, "txt", "line0", text)


def _write_csv(path: Path, rows: list[list[str]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


def generate(seed: int, split: str, out_dir: Path, size: str = "medium") -> dict:
    rng = random.Random(seed)
    n = {"small": 4, "medium": 8, "large": 14}[size]
    out_dir.mkdir(parents=True, exist_ok=True)
    truth = Truth(seed, split)

    family_a(rng, out_dir, truth, n)
    family_b(rng, out_dir, truth, n)
    family_c(rng, out_dir, truth, n)
    family_d(rng, out_dir, truth, n)
    family_e(rng, out_dir, truth, n)
    registry_seed_patients = [make_patient(rng, f"F-SEED-{i:03d}") for i in range(max(2, n // 2))]
    for sp in registry_seed_patients:
        rel = "registry_seed/registry_seed_patients.csv"
        _append_registry_row(out_dir / rel, sp, truth)
    family_f(rng, out_dir, truth, n, registry_seed_patients)
    family_g(rng, out_dir, truth)
    plant_canaries(rng, out_dir, truth)
    clean_controls(rng, out_dir, truth)

    return truth.dump()


def _append_registry_row(path: Path, p: Patient, truth: Truth):
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        if is_new:
            w.writerow(["Name", "DOB", "Village", "Phone"])
        w.writerow([p.name_latin, dmy(p.dob), p.village, p.phone])
    rel = "registry_seed/registry_seed_patients.csv"
    loc = f"row:{sum(1 for _ in open(path, encoding='utf-8')) - 2}"
    truth.entity(rel, "csv", loc, type_="PATIENT_NAME", surface=p.name_latin, script="latin", channel="content")
    truth.entity(rel, "csv", loc, type_="DOB", surface=dmy(p.dob), script="latin", channel="content")
    truth.entity(rel, "csv", loc, type_="PHONE", surface=p.phone, script="latin", channel="content")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", required=True, choices=["dev", "validation", "blind"])
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", default="medium", choices=["small", "medium", "large"])
    a = ap.parse_args()
    out_root = Path(a.out)
    input_dir = out_root / "input"
    truth = generate(a.seed, a.split, input_dir, a.size)
    (out_root / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    n_files = len(truth["files"])
    n_entities = sum(len(u["entities"]) for f in truth["files"].values() for u in f["units"].values())
    print(f"[{a.split}] seed={a.seed} size={a.size}: {n_files} files, {n_entities} PII entities, "
          f"{len(truth['registry'])} registry rows, {len(truth['canaries'])} canary surfaces -> {input_dir}")


if __name__ == "__main__":
    main()
