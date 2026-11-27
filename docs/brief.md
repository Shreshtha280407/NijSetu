# NijSetu: a context-aware privacy firewall for multilingual hospital records

**Track 3 · Secret Wards** · Members: `<fill in: team member names>` · Repo: https://github.com/Shreshtha280407/NijSetu

## 1. In one paragraph

NijSetu ingests CSV/TSV/JSON/FHIR R4/text exports from four fictional hospital systems — mixing
English, Devanagari Hindi and Hinglish in the same sentence — strips every piece of personal
data while keeping every medical value exactly intact, links the same patient across languages,
files and hospitals with keyed-HMAC pseudonyms, and quantifies (not assumes) the privacy it
bought: a real registry-based re-identification measurement ships alongside the release, not
just a claim of "anonymised."

## 2. Architecture

```
input/ (CSV/TSV/JSON/FHIR/TXT, 4 encodings)
   -> Ingestion        (format/encoding detection, malformed-file quarantine)
   -> Detection          (rules + gazetteer + Devanagari transliteration + scoring,
                          per script: latin / deva / hinglish)
   -> Identity linkage   (blocking -> fuzzy/phonetic/cross-script match -> clustering;
                          evidence-gated: no shared strong ID or unique (name + clue) -> no link)
   -> Privacy layer      (per-patient HMAC date shift, age banding, geography
                          generalisation, small-cohort suppression)
   -> Export             (sanitized/, report.json, records.jsonl)
          \-> Security layer wraps every raw/working artifact: AES-256-GCM at rest,
              fail-closed quarantine, PII-free logs, RAKSHA_SECRET never written
```

## 3. How each part works

- **Ingestion:** format- and encoding-aware adapters (UTF-8/UTF-8-BOM/UTF-16/cp1252) for
  CSV/TSV/JSON/FHIR R4 Bundles/free text; anything truncated, malformed or unsupported is
  quarantined, never copied through half-cleaned.
- **Detection:** layered rules + a bundled name/place gazetteer + Devanagari normalisation and
  transliteration + a scoring step that resolves the "meaning traps" (`C/O <relative>` vs.
  `C/O <complaint>`, `Dr. Ramesh` vs. relative `Ramesh`, `1-0-1` dose vs. phone/ID) rather than
  one script-blind regex pass.
- **Linkage & pseudonyms:** blocking keys plus fuzzy/phonetic/cross-script comparison cluster
  records into patients; a cluster is only formed on a shared strong identifier or a
  (name + second clue) combination unique in the dataset — ambiguous evidence is left
  unlinked by design, because a false merge is scored worse than a missed link. IDs are
  issued via keyed HMAC (`RAKSHA_SECRET`), never plain hashing.
- **Privacy layer:** date shifts are per-patient (HMAC-keyed, never a global offset — a global
  shift protects nobody once two dates are recovered), age is banded, geography is generalised
  (village/house/landmark suppressed, district retained), and small cohorts below the
  k-threshold escalate to coarser bands automatically.
- **Security:** AES-256-GCM (the `cryptography` package) protects NijSetu's own raw/working
  files at rest — the dashboard's uploaded files and the CLI's private analysis artifact — each
  under a fresh in-memory key, domain-separated from the HMAC pseudonym key. Decrypted bytes are
  staged only in a directory verified (against `/proc/mounts`, not assumed) to be tmpfs, never on
  persistent disk. Every error is a reason code, never a raw traceback or the secret.

## 4. USP

- **Cross-script identity matching**, not exact-string lookups: the same patient is linked as
  "Mohammad Arif" / "मोहम्मद आरिफ" / "Md. Arif" / "Arif sahab" through transliteration-aware
  blocking, not a name list.
- **Context traps handled explicitly** — relative vs. doctor, complaint vs. care-of, dose vs.
  phone/ID — instead of one regex pass that cannot tell them apart.
- **Measured, not assumed, privacy–utility trade-offs:** a repeatable harness
  (`scripts/policy_tradeoff.py`) reruns the real pipeline under each policy knob and reports
  what it costs in clinical utility and what it buys in quasi-identifier uniqueness against an
  actual registry — see §5.
- **Tested for generalisation, not just the practice set:** an independently authored corpus
  sharing no surface forms with the practice data (new hospitals, header spellings, ID prefixes,
  encodings) is scored against its own answer key.

## 5. Privacy–utility trade-off (measured on the practice data)

| Measure | Value |
|---|---|
| personal data leaked (overall / Latin / Devanagari / Hinglish) | 0.0% / 0.0% / 0.0% / 0.0% |
| planted canaries found | 0 of 4 |
| medical values kept | 97.3% (4,223 / 4,339) |
| linking precision / recall / F0.5 | 1.00 / 0.74 / 0.93 (0 false merges) |
| raw/unsupported files copied through unchanged | 0 |
| re-identification risk (shipped "balanced" policy) | 4 of 788 released records (0.5%) land in a (district × 10-yr age band × gender) cohort matching exactly **one** person in a 240-person practice registry; 5 (0.6%) match fewer than three |
| re-identification risk, geography suppressed entirely | 0 of 788 (same clinical-utility cost: 97.3% -> included for comparison, not shipped) |
| generalisation to an independently authored, never-tuned-on corpus | 0 / 176 leaks; 35 / 35 clinical values retained |
| run time / peak memory (200-file practice set) | 9.3 s / 54.5 MB |

The shipped "balanced" policy (10-year age bands, district retained, ±30-day per-patient date
shift) keeps 97.3% of clinical utility while leaving only 4 of 788 released records
singly-identifiable against the registry on quasi-identifiers alone — the residual is the
**Re-identification Trap**: rare (district, age-band, gender) combinations that survive even
after date shifting. Suppressing geography entirely removes those 4–5 records but is not the
shipped default, because it is a district-wide utility cost paid to fix a handful of rare rows;
the honest alternative — suppressing only the rare cohorts — is exactly the k_threshold
escalation NijSetu already runs, and §6 states where it still falls short.

## 6. Known limitations

- **Linking recall is 0.74 by design.** Roughly a quarter of truly-linkable records are left
  unlinked when the evidence (name + one clue) is not unique in the dataset, because a missed
  link costs less than a false merge. Some genuinely-same-patient records across languages will
  therefore surface under separate pseudonyms rather than be wrongly merged.
- **A handful of rare cohorts remain singly-identifiable.** The automatic small-cohort
  escalation (k_threshold = 3) did not fully close the gap the external registry measurement
  found (§5): 4 of 788 released records still land in a unique (district, age-band, gender)
  cohort. Closing it needs suppression of geography on those specific rare rows, not a
  dataset-wide policy change.
- **Practice-tuned rules lose ground on genuinely novel formats.** The organizer's own
  calibration table shows even a strong reference implementation loses real points on the
  unseen-format round; NijSetu's heldout-corpus test (0/176 leaks) is independent evidence of
  generalisation, not proof the hidden "never seen" round will score identically.
- **This brief's measurements are practice-only.** They come from `dev/input` (200 files, 48
  patients) with the organizer's own `check_dev.py` and this repo's `policy_tradeoff.py` against
  a 240-person registry — not the hidden evaluation datasets, which are larger and have new
  patients.
