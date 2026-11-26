# NijSetu — Gate 0.5 Final Report

**Privacy, identity and evaluator hardening**

> **Gate 0.5 evaluation snapshot. Reviewed 27 November 2026.** This is the
> latest recorded engine evaluation, not a new practice-corpus run on each
> documentation review. Gate 0.5 and the synthetic frontend have since been
> committed and merged into `main` (PRs #1 and #2). Current setup and fresh
> verification are in the [root README](README.md), with dashboard scope in
> the [frontend guide](frontend/README.md). The README and dashboard are
> complete; engine/API integration and the two-page Project Brief PDF remain
> outstanding. Session-state statements below are historical.

Original session branch: `phase/gate05-privacy-linkage-hardening`, off `main`
at `b0358f3`. Work was left in the working tree for review at that point.

Experiment ledger with hypotheses, rejected approaches and before/after
measurements for each change: [`docs/gate05-experiments.md`](docs/gate05-experiments.md).

---

## A. Verdict

**PASS.**

Every objective in the brief is met and measured, both safeguards hold
(precision 1.00, zero false merges; clinical preservation 97.3%), and the
result is reproduced on an independent corpus the rules were never developed
against. Section H states what is still wrong and what the verdict does not
cover — in particular that the hidden adversarial attacker score is unknown and
is **not** claimed here.

---

## B. Before / after scorecard

Organizer practice corpus (200 files, 3230 scored entities), measured with the
organizer's own `check_dev.py`. "Gate 0" is the state this phase started from.

| | Gate 0 | **Gate 0.5** | |
|---|---|---|---|
| **Entities leaked** | 243 / 3230 — 7.5% | **0 / 3230 — 0.0%** | ✅ |
| MRN | 11.3% | **0.0%** | |
| PATIENT_NAME | 4.7% | **0.0%** | |
| PHONE | 12.7% | **0.0%** | |
| VILLAGE / LOCALITY / HOUSE / LANDMARK | up to 20.7% | **0.0%** | |
| Aadhaar / ABHA / PAN / PIN / DOB / email | 0–0.7% | **0.0%** | |
| by language — latin / देवनागरी / hinglish | 6.7% / 16.1% / 18.0% | **0.0% / 0.0% / 0.0%** | |
| by channel — content / filename / base64 / metadata / key / header | 6.2% / 64.6% / 10.0% / 17.5% / 50.0% / 50.0% | **0.0% × 6** | |
| **Canaries leaked** (−3 each) | **3 / 4** → −9 pts | **0 / 4** → 0 pts | ✅ |
| **Raw unprocessed exports** (−5 each) | 0 | **0** | ✅ |
| **Clinical values preserved** | 95.8% (3518/3671) | **97.3% (4223/4339)** | ✅ |
| **Identity — pairwise precision** | 0.990 | **1.00** | ✅ |
| **Identity — pairwise recall** | 0.213 | **0.74** | ✅ |
| **Identity — F0.5** | 0.57 | **0.93** | ✅ |
| **Pseudonyms wrongly merging two people** | 1 | **0** | ✅ |
| Records linked | — | 769 / 788 published claims | |
| Files quarantined | 30 / 200 | **11 / 200** | ✅ |
| Output contract | OK | **OK** | ✅ |
| pytest | 281 pass / 3 fail | **457 pass / 0 fail** | ✅ |
| Wall clock / peak RSS | 2.57 s / 57.6 MB | **5.3 s / 56.6 MB** | ✅ |

Against the brief's stated targets: precision ≥0.98 → **1.00**; F0.5 ≥0.75
(ambitious) → **0.93**; zero false merges on the hard-negative suite → **0**.

The Gate 0 column uses the ledger's **E0 baseline**, rather than intermediate
experiment results that had previously been mixed into this table.
Two numbers are corrections to `GATE0_FINAL_REPORT.md`,
which understated both: it reported 2 of 4 canaries (actually **3**) and 0
quarantined files (actually **30** — the report had conflated the raw-copy
penalty count with the quarantine count). The corrected baseline is used
throughout; see the ledger's "accounting corrections".

---

## C. What was done, by objective

### A — Encoded-identifier leaks (`src/nijsetu/detect/encoded.py`)

Base64 payloads are decoded under explicit bounds (1 MiB input, 768 KiB
decoded, depth 3, JSON nesting 64) and classified: clean payloads pass through
**byte-identical**, PII-only payloads are suppressed, and a payload carrying
both PII and clinical content is sanitized and **re-encoded preserving its
original alphabet and padding**, then independently re-scanned for residual
PII. `base64` channel 10.0% → **0.0%**. 37 tests, including minted base32
tokens, SHA-256 digests, `g/dL`, `112/78` and malformed padding as negatives.

### B — Generalized multilingual PII detection

Seven root causes, seven structural rules — not value lists, because the
organizer states the hidden datasets differ in exactly the surface forms a
value list would encode:

* **Label-free identifier shape** — uppercase mnemonic + separators + digit
  run, requiring ≥2 uppercase letters and ≥5 digits. **Validated against the
  answer key before adoption**: 820 of 946 corpus matches are entities marked
  for redaction, 126 are episode/bill numbers the key ignores, and **zero**
  collide with values the key requires preserved.
* **Head-noun header resolution** — `hospital_uhid` now resolves to `uhid`
  (previously `OTHER`, and `OTHER` means *retain*: 16 MRNs published in clear).
  Refused for a lone generic noun, so `test name` does not become a patient
  name and destroy every lab test.
* **Devanagari label equivalents** (`आईपीडी सं.:`), and the Hindi
  number-words as non-names — they were being redacted *as* names, which
  destroyed the label and left the identifier after it unrecognised.
* **`is_field_label`**, derived from the existing `HEADER_ALIASES` table, so a
  label the ingest layer knows is automatically known to free text.
* **`find_organisation_spans`** — institution names recognised by generic type
  nouns. The two facility brand words previously hardcoded in a stoplist
  (`"jyoti"`, `"sadan"`) were **removed**: they are real given names.
* **Village / ward / landmark labels** in both scripts, landmarks recognised by
  spatial preposition, and unlabelled segments of a line already shown to be an
  address.
* **Corpus-derived name gazetteer** — the tokens of every structurally-declared
  person name in the input, at the weakest evidence tier. The bundled gazetteer
  cannot hold every Indian given name (`Geeta`, `Irfan`, `Rukhsana` are all
  absent and all leaked); the corpus's own labelled fields supply what it
  lacks. The tier is what keeps a patient named Ramesh from turning every
  attending `Dr. Ramesh` into a patient name.

### C — Record-specific pseudonym verification (`src/nijsetu/record_locator.py`)

Gate 0 verified a pseudonym claim by searching the **whole file**, which passes
whenever any record in the file contains the token — including when two
patients' pseudonyms are swapped. Replaced with verification of the exact
addressed record: RFC 6901 pointer resolution accepting only canonical decimal
indices, a real CSV reader so a quoted multi-line cell cannot desynchronize a
row index, token matching against **values and not keys**, and duplicate-locator
detection. On the practice corpus: **788 claims, 0 rejections**, and
`record_claim_rejections` in `report.json` discloses any abstention rather than
silently downgrading it. 31 tests, every one written so it would *pass* under
whole-file search and must *fail* under correct verification.

### D — Identity resolution (precision-first)

F0.5 0.57 → **0.93** with precision held at **1.00** and **zero** false merges.
Three causes, all measured: one-edge-per-record merging with the margin
evaluated *before* any merge in the batch (a patient's own scattered fragments
counted as rival patients); event-level identifiers used as identity anchors
(one patient owns many specimens, so normal repeat activity became an
`ID_CONFLICT`); and per-file issuer scoping with no value-level evidence.

Now: agglomerative merging to convergence with cluster state re-read live; the
runner-up margin restored but evaluated against *different* live clusters and
only for one-token name matches; issuer-independent identifier **value**
agreement as corroborating evidence; and four contradiction vetoes, each
requiring the evidence present on *both* sides — birth year (absolute), phone,
same-facility-series identifier, and **relative name** (same relationship role,
disagreeing names), which is the only evidence that separates the organizer's
planted homonym pair.

Two approaches were **rejected** rather than tuned into submission:
prefix-as-issuer (collapsed two unrelated hospitals that both number patients
`LOCAL-001`, and silently overrode the configured `--source-system`
declaration) and a two-agreeing-token floor (killed the homonym trap but also
the legitimate bare `Arif sahab` a ward note has nothing else to link by).

The mid-experiment state that broke the safeguard — **0.88 precision, 7 wrong
merges** — is recorded in the ledger rather than omitted. It is what the
relaxed merge gate produced before the vetoes existed, and it is the reason the
vetoes are not optional.

`CORRELATED_HOUSEHOLD` was removed from the admissible merge tiers. The tier
means *one household, different people*; admitting it merged two patients who
share the surname Pandey.

**15 hard negatives** (`tests/test_identity_hard_negatives.py`), each an
end-to-end pipeline run: identical full names separated by spouse / phone /
birth year; same-series different numbers; a family sharing one phone; a shared
address and surname; a recycled `LOCAL-001` at two unconfigured hospitals; a
generic `UHID-` prefix that is not a facility series; a bare given name fitting
two patients; an A–B–C bridge whose ends conflict; a shared facility banner;
shared Hindi field labels; a shared attending physician — **plus the mirror
case**, one patient's many specimens, so the precision fixes cannot be paid for
with silent recall loss. Two of these found real holes before the code passed
them.

### E — Privacy/utility policy, measured

Seven policy variants run end to end through the same code path the submission
uses, scored for clinical preservation, records retained, quarantines, and —
as the privacy side — **population uniqueness against the organizer's own
240-person attacker registry**. Full table in the ledger. Headline: suppressing
district eliminates all 4 measured uniquely-identifiable released rows for 1.1
points of clinical utility; the full `strict` preset reaches the same privacy
result for 11.4 points. Shipped policy keeps `geography_level: district`, with
the price recorded so the choice can be overturned by one knob.

The ledger's policy table uses `scripts/policy_tradeoff.py`'s batch-wide
substring utility scorer (98.4% for balanced), while section B uses the
organizer's per-file scorer (97.3%). These percentages have different scoring
scopes and are not interchangeable.

Three inconvenient findings are reported rather than dropped: date-shift width
moves nothing in this table (a limitation of the metric, since dates are not in
the measured quasi-identifier set — the shift is retained on reasoning, not on
this measurement); age-band width moves `k=1` the wrong way, which is a binning
artefact; and the shipped `k_threshold: 3` widening structurally cannot catch
these rows, because it measures cohort size within the release while the
attacker's k is in the population.

### F — Accounting reconciliation

`records_quarantined` was reported as `0` while eleven files were quarantined,
and `records_processed` summed only released plus suppressed records, so those
records appeared in no total. Fixed, and now reconstructed independently from
the released bytes by 9 tests: `1149 == 997 + 128 + 24`,
`200 == 189 + 11`, `files_sanitized == files on disk`,
`record_claims_published == lines in records.jsonl`, and every claim pointing
at a file that was actually published.

`record_claims_published` is now published separately from `records_released`
and the 997 ≠ 788 gap is **explained**: a claim addresses a *location*, and a
FHIR Bundle is one location carrying many resources. A test asserts claims ≤
released, since an inversion would mean a claim for a record never released.

Also found: the practice corpus contains two **byte-identical** input files, and
`report.json` keys inputs by `input_sha256`. Both copies correctly get their own
entry and output, but any consumer building a `{sha: output}` dictionary
resolves one of the pair to the other's output. The ambiguity is in the
organizer's schema; it is pinned in a test so the duplicate entries are never
"fixed" away.

### Also fixed along the way

* **The largest single utility defect in Gate 0**: 283 of 907 dates (31.2%)
  were destroyed by per-value `DATE_AMBIGUOUS`. Day-first/month-first is now
  inferred per *scope* (column / key / document) from an evidence cascade
  — label-declared, then values in scope, then file, then corpus. Billing
  length-of-stay went from 1 of 5 rows to 9 of 9.
* **The only binary dependency was eliminated.** RapidFuzz is now optional
  (`pip install .[speed]`); the pure-Python fallback is asserted *equivalent*
  on 3000 random pairs per function plus real Indic variants, and comparison
  tests **skip rather than pass** when RapidFuzz is absent. The wheel is
  `py3-none-any` and installs fully offline.
* **17 wrongly-quarantined files recovered** by fixing four independent causes:
  a blood-pressure/date-fragment false positive, a short-numeric literal false
  positive, path-shaped metadata, and Hinglish headers.
* **A CSV ambiguity guard that was publishing PII.** Two colliding identifier
  columns were both demoted to `OTHER` — which means *retain* — publishing
  **230 MRNs verbatim**. The guard now keeps the role and reports the ambiguity.

---

## D. Does this generalize, or is it fitted to the practice corpus?

This is the question that matters, because every rule above was developed while
measuring against the practice corpus, and 0.0% on that corpus is exactly the
number most at risk of meaning nothing.

`scripts/make_heldout_corpus.py` generates an **independent** 16-file corpus
with its own answer key, sharing no surface forms with the practice data —
asserted by a test, not assumed. New names, villages, hospitals and doctors;
new identifier prefixes (KGH/NVH/MCN, never JJH/SNJ/DHH/ADM/IP); new header
spellings (`Patient's Full Name`, `Contact#`, `Gram/Mohalla`, `Parchi
Sankhya`); cp1252 and utf-16 encodings; day-first, month-first and ISO dates;
and all six scored channels carrying at least one entity each.

```
$ python scripts/make_heldout_corpus.py <dir>
wrote 16 files, 176 entities, 35 clinical values

$ RAKSHA_SECRET=heldout-secret python main.py --input <dir>/input --output <out>
$ python scripts/score_heldout.py <dir> <out>
files: 16  exported: 16  quarantined: 0
LEAKED: 0 of 176 entities (0.0%)
  by language: deva 0.0%, hinglish 0.0%, latin 0.0%
  by channel:  base64 0/4, content 0/147, filename 0/11, header 0/6, key 0/4, metadata 0/4
CLINICAL VALUES KEPT: 35 / 35 (100.0%)
```

Zero leaks, zero quarantines, no clinical loss, on data the rules had never
seen. That is the strongest evidence available that these rules recognise
*shapes* rather than *values*. **It is not proof**: the corpus is written by
the same author as the rules, so it cannot contain a construction neither
thought of. Locked in by `tests/test_heldout_generalization.py`, which
regenerates the corpus, runs the real entrypoint, and asserts each channel
separately — an aggregate rate hides the one failure that matters, a single
channel at 100%.

---

## E. Evaluator evidence — real commands, real exit codes

All run from the repository root on the practice data, final working tree.

```
$ python -m pytest tests/ -q
457 passed in 26.15s                                            exit 0

$ python -m ruff check src/ scripts/ tests/
All checks passed!                                              exit 0

$ python -m nijsetu gate phase1 --output <dir>
14 checks, 0 failures                                           exit 0

$ python -m nijsetu gate phase2 --output <dir>
0 failures                                                      exit 0

$ RAKSHA_SECRET=practice python main.py \
    --input ../Secret-Wards-ToolKit/dev/input --output <out>
wall 5.26 s   cpu 5.20 s   peak RSS 56.6 MB                     exit 0

$ python validate_output.py --input dev/input --output <out>
CONTRACT OK                                                     exit 0

$ python check_dev.py --output <out> --show 5
1) PERSONAL DATA: 0 of 3230 pieces still visible (0.0% leaked)
   by language: latin 0.0%, deva 0.0%, hinglish 0.0%
   by hiding place: content 0.0%, filename 0.0%, base64 0.0%,
                    metadata 0.0%, key 0.0%, header 0.0%
2) CANARIES: 0 of 4 planted canaries found
3) MEDICAL DATA KEPT: 4223 of 4339 medical values survived (97.3%)
4) PATIENT LINKING: precision 1.00, recall 0.74, F0.5 0.93
                    (pseudonyms that wrongly merge different people: 0)
5) RAW COPIES: 0                                                exit 0

$ python check_submission.py --repo . --run
READY TO SUBMIT                                                exit 0
```

**Robustness checks**, each run and verified:

| Check | Method | Result |
|---|---|---|
| Determinism | two runs, same secret, SHA-256 of every output file | **190 / 190 identical** |
| Secret rotation | different secret | 605 tokens and 140 pseudonyms, **0 shared** |
| Resource limits | `taskset -c 0,1` + `ulimit -v 4194304` (2 CPU, 4 GiB) | exit 0, 5.30 s, 56.8 MB |
| Offline | `socket.socket`, `create_connection` and `getaddrinfo` all raise | exit 0 |
| Budget headroom | 5.3 s of 900 s wall, 57 MB of 4096 MB | **0.6% / 1.4%** |

Note on `check_submission.py` output:

* **"Project Brief PDF found" is a false positive.** It matched
  `tests/fixtures/phase2/dev/malformed_and_unsupported/scan.pdf`, a deliberately
  corrupt test fixture. **No project brief exists**; it is out of scope for this
  phase and is still owed.

---

## F. Tests added

| File | Tests | Guards |
|---|---|---|
| `test_record_locator.py` | 31 | Objective C — each would pass under whole-file search and must fail under correct verification |
| `test_encoded_payloads.py` | 37 | Objective A — bounds, round-trips, classification, and negatives that must never be decoded |
| `test_identity_hard_negatives.py` | 15 | Pairs that must never become one patient, plus the mirror recall case |
| `test_detect_gate05_routes.py` | 66 | Every new detection route, with the clinical value each must not destroy |
| `test_heldout_generalization.py` | 5 | Independent corpus, per-channel, plus corpus disjointness |
| `test_report_accounting.py` | 9 | Every report total reconstructed from the released bytes |
| `test_similarity_fallback.py` | 8 | Pure-Python fuzzy matching equivalent on 3000 random pairs per function |

Total **281 → 457**, with the 3 pre-existing failures fixed.

---

## G. Integrity statement

* No organizer canary literal is hardcoded anywhere. The canaries are defeated
  by two general routes (base64 decoding, dictionary-free name recognition),
  and the two facility brand words that *were* hardcoded in a stoplist were
  **removed**, not extended.
* No answer-key data is embedded in the system. The answer key was used twice,
  both times as *validation of a rule before adoption* and both times recorded:
  to confirm the label-free identifier shape collides with zero
  preserve-list values, and to identify which clinical values a change
  destroyed. No key value is referenced by any shipped code path.
* No validation standard was lowered to pass. One test assertion was inverted —
  `test_csv_ambiguous_alias_headers_not_guessed` — because the behaviour it
  pinned was publishing 230 MRNs in clear; the replacement asserts the safe
  behaviour with the rationale written out.
* Every regression is in the ledger, including the three that cost the most to
  find: 0.88 precision with 7 wrong merges, recall halving to 0.11 twice, and a
  clustering rewrite that deadlocked.
* **The hidden adversarial attacker score is not known and is not claimed.**
  Section C's Objective E reports uniqueness against the *practice* attacker
  registry only.
* Nothing was committed or pushed. No history was rewritten. The previous
  development repository was not touched.

---

## H. Remaining limitations

1. **Every measurement is on synthetic practice data, iterated against.** The
   held-out corpus (section D) is the mitigation, and it is written by the same
   author as the rules. A construction neither it nor I thought of is the most
   likely source of a hidden-dataset leak.
2. **Four released rows remain uniquely identifiable** against the practice
   attacker registry. Measured, priced (1.1 points of clinical utility to
   remove), and deliberately not removed — see Objective E. The
   small-cohort widening structurally cannot catch them.
3. **Eleven files are still quarantined**, of which 3 are `UNSUPPORTED_EXTENSION`
   and 2 `UNSUPPORTED_ENCODING` (correct), 2 are malformed JSON (correct), and
   **4 are conservative holds**: 3 `RESIDUAL_LEAK_DETECTED` and 1
   `JSON_WRAPPER_METADATA_UNSAFE`. Those four are safe (nothing leaks) but cost
   their clinical content. At least one is a *false* residual leak — a retained
   attending physician's name colliding with a different patient's given name
   in the same file, which the per-file literal scan cannot distinguish.
4. **Identity recall is 0.74, not 1.0.** 19 of 788 published claims carry no
   pseudonym, and recall is bounded by deliberate abstentions:
   `UNCORROBORATED_BARE_NAME`, `TIED_CANDIDATES`,
   `NO_IDENTIFIER_CORROBORATION`. Each is a case where two readings are equally
   supported; the system declines rather than guesses. Raising recall from here
   means accepting merges that the hard-negative suite rejects.
5. **The label-free identifier route requires uppercase in prose.** A lowercase
   minted identifier in document text is missed. The case requirement is
   dropped for filenames (where it buys nothing) but kept in prose, where it is
   what stops clinical codes being redacted.
6. **Relationship roles are only `spouse` and `parent`.** A sibling or
   guardian named without a role marker contributes no contradiction evidence.
7. **Cluster-wide contradiction checking is O(|A|×|B|)** in `score_pair` calls
   per candidate merge. It dominates the 2.57 s → 5.3 s runtime change.
   Irrelevant at this corpus size (0.6% of budget); it would need indexing at
   10× the records.
8. **Submission brief remains outstanding.** The two-page Project Brief PDF
   is absent. The README and synthetic frontend, which were outside this
   phase's scope, have since been completed. Connecting the dashboard to the
   Python engine remains future work.
9. **Repository status has advanced.** Gate 0.5 and the frontend are committed
   and merged into `main`; the original session's uncommitted-work statements
   above are historical.
