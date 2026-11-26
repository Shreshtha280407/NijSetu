# Gate 0.5 experiment ledger

> **Historical experiment record. Reviewed 27 November 2026.** Environment,
> branch, commit state and intermediate measurements below describe the Gate
> 0.5 session. That work and the synthetic dashboard have since been committed
> and merged into `main`. Current setup and verification are in the
> [root README](../README.md); final engine measurements are collected in the
> [Gate 0.5 report](../GATE05_FINAL_REPORT.md). Intermediate results are
> retained to document regressions and decisions, not presented as current
> test totals or repository state.

Every entry: hypothesis → failure mode → fixture → before → after → cost → decision.
All numbers are measured, never estimated. `check_dev.py` is the organizer's own
practice scorer (rough guide, not the official score); `validate_output.py` is the
contract validator.

## Environment of record

| Item | Value |
|---|---|
| Working repo | `Shreshtha280407/NijSetu` (official) |
| Branch | `phase/gate05-privacy-linkage-hardening` |
| Base commit | `b0358f3` ("Initial commit") |
| Organizer toolkit | `Reinforce-SST/Secret-Wards-ToolKit` @ `6c0a85a` |
| Interpreter | CPython 3.14.8 (Linux x86_64) |
| Budget | 2 CPU / 4096 MB / 900 s (`scoring.toml [limits]`) |

## E0 — Baseline of record (measured, not inherited)

`RAKSHA_SECRET=practice .venv/bin/python main.py --input <tk>/dev/input --output <out>`

| Metric | GATE0 report claimed | **Measured** | Verdict |
|---|---|---|---|
| PII still visible | 7.5% | **7.5%** (243/3230) | reproduced |
| Clinical preservation | 95.8% | **95.8%** (3518/3671) | reproduced |
| Link precision | 0.99 | **0.990** | reproduced |
| Link recall | 0.21 | **0.213** | reproduced |
| Link F0.5 | 0.57 | **0.57** | reproduced |
| Canaries leaked | 2/4 | **3/4** | **report was wrong (under-counted)** |
| Wrongly merged pseudonyms | 1 | **1** | reproduced |
| Files quarantined | "0/200" | **30/200** | report conflated "0 raw copies" with "0 quarantined" |
| Raw unsupported copies | 0 | **0** | reproduced |
| pytest | 284 passing | **281 passed, 3 failed** | all 3 = cp311-vs-cp314 wheel ABI (env, not code) |
| ruff | clean | **clean** | reproduced |
| Wall clock | n/a | **2.57 s** | 0.3% of the 900 s budget |
| Peak RSS | n/a | **57.6 MB** | 1.4% of the 4096 MB budget |
| `validate_output.py` | — | **CONTRACT OK** (exit 0) | |
| `check_submission.py` | — | **READY TO SUBMIT** (exit 0) | Recorded submission-contract check |

Per-channel leak baseline: `content 6.2%, filename 64.6%, base64 10.0%,
metadata 17.5%, key 50.0%, header 50.0%`.
Per-script: `latin 6.7%, deva 16.1%, hinglish 18.0%`.

### Accounting corrections to the Gate 0 report (Objective F)

1. **Canaries: 3 of 4 leaked, not 2.** Independently re-measured with the
   organizer's own scorer. The report's claim was too favourable.
2. **30 of 200 files are quarantined, not 0.** The report's "0/200" was the
   *raw-copy penalty* count (`copied_raw`, which only counts an
   `expect:"either"` file exported byte-for-byte). These are different
   denominators and the report used one label for both.
3. **The `check_submission.py` "Project Brief PDF found" line is a false
   positive**: it matched `tests/fixtures/phase2/dev/malformed_and_unsupported/scan.pdf`,
   a synthetic test fixture, not a real brief. The brief is deliberately
   deferred to final submission prep, so this is recorded, not "fixed".
4. **Headroom, not a constraint.** Runtime is 2.57 s against a 900 s limit and
   57.6 MB against 4096 MB. No optimisation decision in this gate needs to be
   driven by cost.

## D0 — Root-cause diagnosis (why the baseline scores what it does)

Measured, per-defect, with instrumented runs over the real practice corpus.

### D0.1 Identity: the bottleneck is **fragmentation, not coverage**

| Measurement | Value |
|---|---|
| truth units with `link:"required"` and a patient | 771 |
| …given a non-null pseudonym by us | **651 (84.4%)** |
| true patients among those units | 48 |
| **distinct pseudonyms we emitted** | **232** |
| mean distinct pseudonyms per true patient | **4.90** |
| singleton pseudonym clusters | 107 of 232 |
| true pairs / tp / fp / fn | 6892 / 1470 / **15** / 5422 |
| pseudonyms spanning >1 true patient | **1** |

Conclusion: candidate/locator coverage is *not* the limit — 84.4% of required
units already carry a pseudonym. Recall is 0.213 because each patient is split
into ~5 clusters. Precision is 0.990 with only 15 false-positive pairs, so there
is large *safe* headroom to merge. Lowering a score threshold is the wrong lever;
the fragmentation mechanism is.

Two concrete mechanisms found in code:

* `identity.resolve_fallback_issuer` scopes an unknown issuer **per input file**
  (`UNKNOWN_SOURCE:<path>`), so the same hospital MRN in `registration/…tsv` and
  `discharge/…txt` lands in two different namespaces and never forms an exact
  edge. `organizer._infer_source_system_map` does recover 101 of 200 files into
  4 inferred issuers (`SRC:jeevanjyoti` 48, `SRC:sanjeevani` 32, `SRC:arogya` 16,
  `SRC:dhhardoi` 5), so the remaining 99 files stay per-file scoped.
* `cluster.attach_fuzzy_edges` takes **at most one edge per record** (its best)
  and then suppresses it when a runner-up is within `runner_up_margin`. Several
  *fragments of the same patient* compete with each other as separate roots, so
  mutually-consistent evidence is read as ambiguity and discarded
  (`TIED_CANDIDATES`). The roots are compared before any of the merges in the
  batch are applied, so the outcome is order-dependent.

### D0.2 Quarantine: 24 of 30 quarantined files are `expect:"sanitize"`

| Reason | Files | `expect` |
|---|---:|---|
| `RESIDUAL_LEAK_DETECTED` | 16 | sanitize |
| `UNPROCESSED` (all records held) | 6 | sanitize |
| `UNSUPPORTED_EXTENSION` | 3 | either |
| `UNSUPPORTED_ENCODING` | 2 | 1 sanitize (`registration/arogya_legacy_export.csv`, cp1252), 1 either |
| `JSON_DEPTH_EXCEEDED` | 1 | either |
| `JSON_SYNTAX_ERROR` | 1 | either |
| `JSON_WRAPPER_METADATA_UNSAFE` | 1 | sanitize (`registration/dhh_patients.json`) |

Quarantining an `expect:"sanitize"` file costs four ways at once:

1. its records vanish from `records.jsonl` → identity recall (95 required units lost);
2. its `preserve` values can never be found → clinical utility
   (`check_dev` only scores preservation `if exists`);
3. integrity loses `quarantine_handling` credit;
4. **its filename leaks anyway.** `check_dev.py` resolves the output path as
   `renamed.get(sha)` and falls back to the *original input path* when
   `report.json` declares `"output": null`. So a quarantined
   `discharge/Discharge_Summary_Fatima_Bano_SNJ-26-064508.txt` still scores its
   filename-channel name and MRN as leaked. This is the dominant cause of the
   **64.6% filename leak rate** — not the exported names, which are already
   opaque (`_renamed/file_N.ext`).

### D0.3 The 283-date bug (largest single utility defect)

`phase2.dates.parse_flexible_date` tries `%d/%m/%Y` and `%m/%d/%Y` on every value
independently and raises `DATE_AMBIGUOUS` whenever both parse to different dates —
i.e. for **every date whose day and month are both ≤ 12**. `actions.decide_feature_action`
turns that into `SUPPRESS … omit_field=True`, so the value is written out **empty**.

Measured over `dev/input`: **283 of 907** (`31.2%`) slash/dash `d/m/yyyy` dates are
dropped entirely. 279 ISO dates are unaffected.

Consequences chained from this one bug:

* `billing/sanjeevani_claims.csv`: input has **9/9** rows with both
  `admission_date` and `discharge_date`; output has **1/5**. Length-of-stay is
  destroyed (`utility.temporal` = 0.20 of the utility weight).
* 7 billing records then fail `CLINICAL_INTERVAL_DATES_MISSING_AFTER_TRANSFORM`
  and are held → they are the `loc_absent[csv]` gaps in `records.jsonl`.
* Top affected files are the lab CSVs (`lifeline_results.csv` 63,
  `sanjeevani_results.csv` 46, `city_results.csv` 44, `pathkind_results.csv` 42).

The fix must infer the convention from a **scope** (column/file), not from a single
value, because a single ambiguous value genuinely carries no information.

### D0.4 Residual-leak quarantines decompose into three unrelated bugs

Captured by instrumenting `leak_scan.scan_literals` on the real corpus:

1. **Path-like metadata values are not sanitized as paths** (14 of 16 files).
   `opd/*.json` carries
   `"source_path": "D:\\HIS\\exports\\opd\\Kavita_[REDACTED_NAME]_064371\\visits_2026_01.json"` —
   the surname is redacted but the **first name and the MRN digits survive**,
   because the value is scanned as prose and `_`-joined tokens have no `\b`
   boundary between them. `organizer._normalize_for_match` already solves exactly
   this for *filenames*; it is not applied to path-shaped *content*.
2. **Short/numeric literals produce false leak findings** (`registration/jeevanjyoti_patients.tsv`).
   `leak_scan._clean` admits any literal with `len >= 3`, so 3-digit house numbers
   (`163`, `192`, `310`, `317`, `346`, `398`) are searched as bare substrings and
   match inside unrelated phone digits (`060062-72163`) and **inside our own minted
   base32 tokens** (`…te346nnoj…`). One such false positive quarantines a file
   holding **30 required-link units**. The `minted_tokens` exclusion exists for the
   generic pattern scan but not for the literal scan.
3. **An unlabelled in-sentence Devanagari name survives** (`prescriptions/rx_sanjeevani_00049.txt`).
   `कविता पांडे` is redacted where it is labelled but kept mid-sentence
   (`… तारीख: 19/01/2026 कविता पांडे को 4 …`). The literal is already known from the
   same document, so a within-document propagation pass closes it.

Also observed: `Dr. Ramesh Srivastava` is flagged because `Ramesh` is a
*patient-name* literal elsewhere in the same file. `check_dev` matches the full
surface (`Ramesh Kumar`), so our scanner is **stricter than the grader** here and
quarantines a file over a retained doctor's first name.

### D0.5 Blood-pressure false positive on a date fragment

`CLINICAL_MEASUREMENT_LOST_OR_CHANGED` holds 63 records. For the 6 discharge
files the lost "measurement" is `[BLOOD_PRESSURE] '30/10'` — a fragment of a
`30/10/2026` date. It is detected as BP in the original, the date is then shifted,
the bogus measurement no longer re-derives from the output, the invariant fails,
the only record in the document is held, and the whole file is quarantined
(`UNPROCESSED`). `30/10` is not a physiologically possible blood pressure.

### D0.6 Insurance identifiers leak inconsistently

`billing/*.csv` output shows `pmjay_id` sometimes intact
(`PMJAY-UP-3849173458`), sometimes truncated to the bare prefix `PMJAY-UP-`, and
`policy_no` retained verbatim (`POL/44/22262805`). Matches the measured
`INSURANCE_ID 50.0%` leak. The prefix-truncation shape indicates the detector
matches only the digit tail of some values and misses others entirely, rather
than claiming the whole field.

---

## Experiments

(Entries appended below as each change is prototyped, measured and kept or reverted.)

### E1 — Scope-level day-first/month-first date convention

**Hypothesis.** `parse_flexible_date` refuses per *value*, but the convention is
a property of the *scope* (column/file/exporter). Resolving per scope should
recover most of the 283 suppressed dates, restoring intervals and un-holding
records, with no privacy cost (every released date is shifted anyway).

**Failure mode.** `DATE_AMBIGUOUS` → `SUPPRESS … omit_field=True` → the cell is
written empty. 283/907 (31.2%) of `d/m/yyyy` dates destroyed; `billing/*.csv`
length-of-stay reduced from 9/9 rows to 1/5.

**Implementation.** New `phase2/date_convention.py`: evidence cascade
(label-declared → value in scope that only one convention parses → same widened
to file → corpus → declared fallback `DMY`). `Feature.date_convention` carries
the decision; `sanitize` resolves once over the whole corpus before any consumer
reads a date. Decision provenance is aggregated into the internal report
(`date_conventions`) so a reader can see which dates rest on the fallback.

**Second-order bug this exposed and fixed.** The clinical-invariant check
re-detects features from the *staged* output; those fresh Features had no
convention, so a correctly shifted date looked unparseable and the record was
held as "interval dates missing". Fixed with `DateConventionResolver.stamp()`
applied at all three staged-reparse sites (CSV/JSON/TXT).

| Metric | Before | After stage 1 (unstamped) | **After stage 2 (stamped)** |
|---|---|---|---|
| Clinical preservation | 95.8% (3518) | 95.3% (3498) | **96.2% (3531)** |
| Identity recall | 0.213 | 0.21 | **0.22** |
| Identity F0.5 | 0.57 | 0.57 | **0.58** |
| INSURANCE_ID leak | 50.0% (11/22) | 31.8% | **68.2% (15/22)** |
| Overall leak | 7.5% | 7.4% | 7.6% |
| `records_suppressed` | 196 | 204 | — |
| Wall / RSS | 2.57 s / 57.6 MB | — | 2.69 s / 57.8 MB |

**Note on the insurance regression.** It is not caused by this change. Billing
rows that were previously *held* (because their interval dates had been
destroyed) are now released, and those rows carry insurance identifiers the
detector never handled. The change surfaced a pre-existing detection gap
instead of hiding it behind a suppressed record. Fixed separately in E3.

**Decision: KEEP.** Utility and identity both improve, contract stays OK, no new
test failures, cost is +0.12 s.

### E2 — Remove the mandatory binary dependency (RapidFuzz optional)

**Hypothesis.** The 3 baseline test failures are one root cause — the vendored
wheel is `cp311`, the interpreter is `cp314` — and NijSetu uses only two
RapidFuzz functions. A tested-equivalent pure-Python fallback removes the whole
failure class and makes the submission installable offline on any interpreter.

**Failure mode.** `pip install --no-index` cannot satisfy `rapidfuzz==3.14.6` for
a non-cp311 ABI, failing `test_package_install`,
`test_phase2_package_install` and the `wheel_offline_gazetteer` check inside
`gate phase1`. The organizer's interpreter version is not knowable in advance.

**Research / alternatives compared.**

| Option | Verdict |
|---|---|
| Vendor cp311+cp312+cp313+cp314 wheels | ~13 MB of binaries, still guesses the ABI set. Rejected. |
| Drop the offline-install test | Lowers the standard to make it green. Rejected. |
| Keep RapidFuzz required, rely on `setup` network | Works (SANDBOX.md allows network in `setup`) but leaves a single point of failure for a 2-function feature. Rejected. |
| **Optional RapidFuzz + exact Python fallback** | **Chosen.** |

`fuzz.ratio` is the indel-normalised similarity
`100·(1 − indel/(|a|+|b|))` with `indel = |a|+|b| − 2·|LCS(a,b)|`;
`token_sort_ratio` is the same over whitespace-sorted tokens. Equivalence was
verified against RapidFuzz on 8,000 random pairs (0 mismatches) before writing
the module, and is now asserted by `tests/test_similarity_fallback.py`
(3,000 random pairs per function plus real Indic transliteration variants).
Those comparisons **skip** rather than pass when RapidFuzz is absent, so a green
run never implies an unchecked equivalence.

| Metric | Before | After |
|---|---|---|
| pytest | 281 passed, **3 failed** | **292 passed, 0 failed** |
| `gate phase1` | FAIL (13/14) exit 2 | **PASS (14/14) exit 0** |
| `gate phase2` | PASS (22/22) | **PASS (22/22)** |
| Mandatory runtime deps | 1 (compiled) | **0** |
| Wheel tag | `cp311` needed at install | **`py3-none-any`** |
| Offline `--no-index` wheel install, fresh venv | fails | **succeeds, `HAVE_RAPIDFUZZ=False`** |
| Output on fallback backend vs RapidFuzz | — | **byte-identical** (`sanitized/` + `records.jsonl`) |
| Wall clock (fallback) | 2.69 s | 3.38 s (+0.69 s; 0.4% of the 900 s budget) |

**Decision: KEEP.** Strictly fewer failure modes, identical output, cost
irrelevant at this scale. RapidFuzz stays declared as an optional `[speed]`
extra and is also listed in `requirements.txt`. The normal `pip install .`
and `raksha.toml` setup install only the dependency-free core. Install
`.[speed]` or `-r requirements.txt` explicitly to enable the accelerator.

### E3 — Detector coverage: labelled identifiers, grouped phones, insurance

**Hypothesis.** The surviving MRN/phone/insurance leaks are a LABEL-vocabulary
problem, not a value-shape problem. A hospital-local id can look like anything
(`IP/26/00130`, `ADM000162`, `DHH-IPD-00591`), so matching on value shape must
either miss unseen schemes or swallow drug codes; what generalises is that the
value is introduced by an episode/registration label.

**Failure modes measured.**

* `UHID: JJH-0077157   IPD No: IP/26/00130` — the UHID was tokenized, the IPD
  number published verbatim. `IPD No`/`IP No`/`ADM No`/`Case No` were in no
  vocabulary, structured or free-text.
* `Mobile: +91 60043 94755` — a *labelled* phone, published verbatim: the phone
  regex only matched the CONTIGUOUS ten digits, never a grouped form.
* `pmjay_id` / `policy_no` columns resolved to no role. `PMJAY-UP-8758791961`
  matched only because its ten-digit tail looked like a mobile, leaving
  `PMJAY-UP-` behind; `PMJAY-UP-3849173458` matched nothing because `3…` is not
  a valid Indian mobile prefix. The 50% leak rate was exactly the fraction of
  tails starting 6-9.

**Implementation.** Broad label vocabulary (English + Hinglish + Devanagari) with
a deliberately generic value pattern; a separate `insurance_id` scheme so a
policy number and a clinical UHID tokenize in different namespaces; structural
grouped-phone recognition (strip separators, then validate ten digits leading
6-9) rather than enumerating group shapes; punctuation/possessive/parenthetical-
insensitive header resolution so `Patient's Name`, `Contact#`, `DOB (MM/DD/YYYY)`
resolve on unseen data.

**Two regressions this caused, and the real bugs behind them.**

1. **230 MRNs published in clear.** Adding `sample id` to the `uhid` alias set
   made `sample_id` and `uhid` collide on one canonical in four lab CSVs. The
   adapter's ambiguity guard then demoted *both* columns to `OTHER` — and
   `OTHER` means RETAIN. The guard's intent (don't guess which column is the
   real UHID) is right; its resolution was the one direction that is never
   safe. Fixed two ways: semantically distinct concepts got distinct canonicals
   (`uhid` / `episode_id` / `specimen_id`, `name_given` / `name_family`,
   `tehsil` / `district`, `city`, `street`), and the guard now KEEPS the role
   so the privacy action still applies while only the field's identity meaning
   is treated as unresolved.
2. **Recall halved (0.22 → 0.11).** The new `specimen_id`/`episode_id` schemes
   became identity anchors, and clustering vetoes a merge when two clusters hold
   different values for one `(issuer, scheme)`. One patient legitimately has many
   specimens and admissions, so ordinary repeat activity became a hard identifier
   contradiction. Fixed by `EVENT_SCOPED_SCHEMES` — event-level ids are still
   detected and tokenized, but never anchor identity. Patient-level schemes keep
   full veto power, which is what still prevents false merges.

**A third bug this surfaced (clinical utility).** `escalate_for_small_groups`
documents itself as "least-disruptive widening" but suppressed *every* coarse
geography field, including the district — the level `geography_level: district`
declares it retains, and which the answer key lists among values that must
survive. It also mattered only now because the old ambiguity guard had been
accidentally preserving districts by demoting them to `OTHER`. Fixed to widen by
one genuine step (drop tehsil, keep district), which required: a `district`
scheme on structured coarse-geography columns (previously only DATE and
PATIENT_IDENTIFIER columns carried a scheme, so a District column was
indistinguishable from a Tehsil column downstream); splitting the trailing PIN
off an address value (`Distt. Rae Bareli - 229073`) so retaining the district
does not publish the PIN with it; and a trigger on "geography changed" rather
than "geography became None" so the risk report never claims a mitigation the
output lacks.

| Metric | Baseline | After E1+E2 | **After E3** |
|---|---|---|---|
| Overall leak | 7.5% (243) | 7.6% (247) | **5.4% (175)** |
| PHONE | 12.7% (39) | 12.7% | **0.3% (1)** |
| INSURANCE_ID | 50.0% (11) | 68.2% (15) | **0.0% (0)** |
| PIN | 0.7% | 0.7% | **0.0%** |
| MRN | 11.3% (96) | 11.3% | **9.2% (78)** |
| PATIENT_NAME | 4.7% | 4.7% | 4.7% |
| VILLAGE | 20.7% | 20.7% | 20.7% |
| latin script | 6.7% | 6.9% | **4.4%** |
| Clinical preservation | 95.8% | 96.2% | **96.2%** |
| Identity precision | 0.99 | 1.00 | **1.00** |
| Identity recall | 0.213 | 0.22 | **0.22** |
| Identity F0.5 | 0.57 | 0.58 | **0.58** |
| Wrong merges | 1 | 0 | **0** |
| pytest | 281p / 3f | 292p | **294p** |
| Wall / RSS | 2.57 s / 57.6 MB | 2.69 s | 2.94 s / 57.6 MB |

**Decision: KEEP.** Strictly better than baseline on every measured axis.
Phone/insurance/PIN essentially eliminated, no clinical or identity cost.
Three real defects (unsafe ambiguity fallback, event-ids as identity anchors,
all-or-nothing geography widening) were found only because the detector change
exposed them; each is fixed at its root with a regression test.

### E4 — Quarantine recovery: stop discarding files the organizer expects sanitized

**Hypothesis.** The 64.6% filename-channel leak rate is not caused by the
exported names (already opaque `_renamed/file_N.ext`) but by *quarantine*:
`check_dev.py` falls back to the ORIGINAL input path whenever `report.json`
declares `"output": null`, so a quarantined
`Discharge_Summary_Fatima_Bano_SNJ-26-064508.txt` still scores its filename
name and MRN as leaked. Recovering the 24 `expect:"sanitize"` quarantines
should therefore improve detection, utility, identity recall AND integrity at
once. Each quarantine is a symptom with its own root cause; none should be
fixed by relaxing the fail-safe.

**Four root causes, each fixed at the root.**

1. **Blood-pressure false positive on a date fragment** (6 discharge files).
   `\b\d{2,3}\s*/\s*\d{2,3}\b` matches `30/10` inside `30/10/2026`. The bogus
   "measurement" is snapshotted from the input, the real date is then shifted,
   the measurement no longer re-derives from the output, the clinical-invariant
   check reports `CLINICAL_MEASUREMENT_LOST_OR_CHANGED`, the document's only
   record is held, and the file is quarantined. Fixed with two independent
   guards: date context (a further `/`+digit on either side) and physiological
   plausibility (systolic 60-300, diastolic 30-200, systolic > diastolic).
   `30/10` fails all three; `126/86` and `90/60` pass.
2. **Literal-scan false positives** (the 30-record TSV, plus others).
   `_clean` admitted any literal of length >= 3, so three-digit house numbers
   (`364`, `163`, `310`) were searched as bare substrings and matched inside
   unrelated phone digits (`060062-72163`) and inside our OWN minted base32
   tokens (`…te346nnoj…`). Fixed with a digit-boundary requirement for short
   numeric literals and a minted-token span exclusion (which
   `scan_generic_residual_patterns` already had and the literal scan did not).
3. **Path-shaped metadata values** (14 files). See `detect/paths.py`:
   `"source_path": "D:\HIS\exports\opd\Anita_Sharma_010565\visits_2026_02.json"`
   had its surname redacted and its given name plus six-digit hospital number
   published, because `_` is a word character and the directory name is one
   undelimited token to every detector. Decided per VALUE now, not per span:
   components are split, boundaries restored length-preservingly, and if any
   component carries PII the WHOLE value is replaced. A provenance path holds
   no clinical information, so this costs nothing; an innocuous path
   (`exports/opd/visits.json`) is left untouched. 15 positive/negative cases
   asserted, including `g/dL`, `112/78`, `OPD/26/02/00085` and `1-0-1`, none of
   which may be mistaken for a path.
4. **Hinglish header vocabulary** (the recovered TSV then leaked 62 entities).
   A UTF-16 registration export uses an entirely romanised header row
   (`Reg No / Naam / Ling / Umar / Janm Tithi / Pita-Pati ka Naam / Pata /
   Gaon / Tehsil / Jila / Pin Code`). `Janm Tithi` matched no DOB alias, so 30
   real dates of birth were published verbatim; `Pata` matched no address
   alias, so the address cell was only partly redacted and its unlabelled
   landmark/house/mohalla components survived. Romanised Hindi spellings are
   now first-class aliases throughout.

**Also added: stated-age banding.** The organizer's guidance is explicit that
age should be published as a band. `age_band` was derivable only from
DOB + reference event, so a record with an age column and no DOB got no band
and the field was suppressed outright — losing a truthful generalization the
score asks for while leaving exact ages elsewhere as a re-identification key
(the attacker's registry carries age). An `age` canonical now routes through
the same generalization path, with a stated-age fallback bounded to a
plausible lifespan.

| Metric | Baseline | After E3 | **After E4** |
|---|---|---|---|
| Files sanitized | 170 / 200 | 170 / 200 | **187 / 200** |
| Quarantine reasons | 7 kinds, 30 files | 30 files | **13 files** (`UNPROCESSED` gone; residual 16 → 5) |
| Overall leak | 7.5% (243) | 5.4% (175) | **5.5% (179)** |
| filename channel | 64.6% | 60.4% | **41.7%** |
| metadata channel | 17.5% | 17.5% | **5.0%** |
| DOB | 0.0% | 0.0% | **0.0%** (was 27.0% mid-experiment, before Hinglish aliases) |
| LANDMARK | 4.7% | 4.7% | 6.2% |
| HOUSE | 2.2% | 2.2% | 3.7% |
| LOCALITY | 8.7% | 8.7% | 13.6% |
| VILLAGE | 20.7% | 20.7% | 24.7% |
| MRN | 11.3% (96) | 9.2% (78) | **7.9% (67)** |
| Clinical preservation | 95.8% (3518/**3671**) | 96.2% (3531/3671) | **96.8% (4272/4412)** |
| `records_linked` | 701 | 708 | **772** |
| `records_suppressed` | 196 | 189 | **125** |
| Identity recall / F0.5 | 0.213 / 0.57 | 0.22 / 0.58 | **0.23 / 0.60** |
| Identity precision / wrong merges | 0.99 / 1 | 1.00 / 0 | **1.00 / 0** |
| pytest | 281p / 3f | 294p | **294p** |
| Wall / RSS | 2.57 s / 57.6 MB | 2.94 s | 3.13 s / 57.7 MB |

**Reading the denominators honestly (Objective F).** Clinical preservation is
`4272/4412`, not `.../3671`. The denominator grew by 741 because
`check_dev.py` only counts a `preserve` value `if exists` — i.e. only in files
that were actually exported. Quarantining a file hid both its leaks and its
preserved values. Releasing 17 more files therefore moves *more* entities into
scope on both sides of the ledger, which is why LANDMARK/HOUSE/LOCALITY/VILLAGE
rise slightly in percentage terms while the absolute leak count falls from
243 to 179 and the absolute preserved count rises from 3518 to 4272. Comparing
only the percentages across these two runs would be comparing different
populations.

**Decision: KEEP.** Every headline number improves against baseline, and the
improvement comes from publishing more of the corpus safely rather than from
suppressing more of it.

### E5 — Objective C: record-specific pseudonym verification

**The defect.** `organizer._build_records_jsonl` verified a claimed pseudonym
with `if token not in file_text_cache[file_no]`, i.e. a substring search over
the whole exported FILE. That is a different claim from the one SPEC.md
section 6 requires ("the `patient` value must actually appear inside that
exported record"), and it is not sound: a token in patient B's row is no
evidence about patient A's row. Every record in a file therefore inherited the
strongest claim any record in it could make, so one correctly-tokenized row
vouched for every other row in the file -- including rows whose pseudonym was
wrong, missing, or belonged to a different person.

**Implementation.** New `src/nijsetu/record_locator.py` plus output locators
threaded through the pipeline:

* every claim now carries `out_loc` (published as SPEC.md's `out`, and only
  when it differs from `loc`), recorded at each emit site: CSV output row index
  excluding the header, JSON output array index, `doc` for text/FHIR;
* the exported bytes are **re-parsed** here and the token is sought only inside
  the resolved row/node -- the transform's own bookkeeping is never consulted,
  so a serialization bug is caught rather than described;
* RFC 6901 handling is correct in the directions that matter: `~1` before `~0`
  on unescape (so `~01` is `~1`, not `/`), escaping on emit for a wrapper key
  containing `/` or `~`, and array indices must be canonical decimal so `/01`
  cannot silently resolve to element 1;
* CSV rows are parsed with the `csv` module, not split on newlines, so a quoted
  multi-line cell cannot desynchronize a row index from its record;
* JSON **keys** are deliberately not searched -- a pseudonym is a value, and a
  key equal to it is a structural coincidence;
* a duplicated `(file_no, out_loc)` rejects **both** claims rather than letting
  whichever was processed last win;
* FHIR keeps the pre-existing rule that a Bundle makes a whole-document claim
  only when every released resource resolves to one patient cluster.

**Abstention policy.** Every rejection downgrades that one claim to
`patient: null` and is counted by reason in `report.json`
(`record_claim_rejections`, counts only -- never a locator, token or patient
value). This follows the organizer's own scoring: "A record you leave unlinked
when the evidence is insufficient costs nothing", whereas an unverifiable
published claim risks a false merge, which is penalised harder. A known-good
link is never nulled to conceal a mapping defect -- the reason code says which
check failed.

**Adversarial test suite** (`tests/test_record_locator.py`, 31 tests). Every
test is written so it would PASS under a whole-file text search and must FAIL
under correct verification, so a regression to file-wide scope breaks the suite
instead of silently restoring the unsound behaviour. Covered: cross-patient
tokens, deliberately swapped pseudonyms (both sides must fail), row index past
end of export, header-row off-by-one, quoted multi-line cells, wrong-delimiter
TSV, non-canonical locators, JSON pointer past end of array, unparseable
export, `~`/`/` escaping, wrapper-object claims, token present only in sibling
metadata, keys-not-values, suppressed-entry index shift, duplicate locators,
unreadable export, and no-claim-for-a-quarantined-file.

| Metric | Before | After |
|---|---|---|
| Verification scope | whole exported file | **the exact exported row / JSON node / document** |
| `out` locator published | never | **when it differs from `loc`** (per SPEC.md) |
| Claims published on practice data | 772 | **772** |
| Claims rejected | n/a (unmeasurable) | **0**, reasons surfaced in `report.json` |
| Identity precision / recall / F0.5 | 1.00 / 0.23 / 0.60 | **1.00 / 0.23 / 0.60** |
| pytest | 294 | **325** |

**Decision: KEEP.** The stricter check published exactly the same 772 claims
with zero rejections, which is the useful result: it confirms the claims were
already record-correct, so correctness was gained without losing any
identity-resolution credit. The unsound code path is gone and is now pinned by
31 adversarial tests.

### E6 — Objective A: encoded (Base64) payloads

**The defect.** A JSON value holding Base64 was treated as an opaque string:

    "attachment": {"contentType": "text/plain",
                   "data": "Q29uc2VudCB3aXRuZXNzZWQgYnkgVHNlcmluZyBXYW5nbW8sIHBob25lIDgwMTA5OTM1MTAu"}

decoding to `Consent witnessed by Tsering Wangmo, phone 8010993510.` and
published **byte-for-byte unchanged**. Base64 is an encoding, not encryption,
and `check_dev.py` itself decodes every long Base64 run before looking for
leaks, so this was a direct disclosure of a planted canary plus a phone number.

**Implementation** (`src/nijsetu/detect/encoded.py` + `transform_json`).
Decision is driven by what the payload *contains*, because blanket suppression
of every Base64-looking value would destroy real clinical attachments (the FHIR
referrals carry `...referred for follow-up of Hypothyroidism, unspecified`):

| Decoded content | Action |
|---|---|
| no PII | left byte-identical |
| PII, no clinical content | whole payload replaced (nothing of measured value lost, no residue possible) |
| PII **and** clinical content | identifying spans redacted, re-encoded in the same alphabet/padding, then **re-scanned**; any residue falls back to suppression |

Handled explicitly: RFC 4648 and URL-safe alphabets (round-tripping in
whichever the input used), optional/absent/malformed padding, encoded UTF-8
text, encoded JSON, and Base64-inside-Base64 with a decrementing depth budget.
Decoding is refused for binary payloads (a real scanned image is never mutated
into something invalid), for mojibake (a printable-ratio floor, so a wrongly
decoded hex digest is not "sanitized" into corruption), and on `validate=True`
so a non-alphabet string is rejected rather than silently skipping characters.

**Bounds, all enforced before the allocation they guard:** encoded input
chars, decoded bytes (checked against the encoded length first), decode depth,
JSON nesting depth, and a minimum run length. Nothing recurses without a
decrementing budget.

**Tests** (`tests/test_encoded_payloads.py`, 37). Positives: encoded names,
Aadhaar, UHID, episode id, ABHA, address components, email, grouped phone,
encoded JSON, nested encoding, URL-safe, unpadded. Negatives that must NOT be
touched: our own minted base32 tokens, a sha256 digest, `g/dL`, `112/78`,
`OPD/26/02/00085`, a dose string, real binary, a clean clinical payload.
Bounds: oversized input, oversized decode, over-deep nesting, truncated and
malformed padding (must not raise). Plus an independent re-scan of the
serialized output, and a check that JSON structure and value types survive
(a suppressed attachment stays a string, never null).

### E7 — Objective B5: dictionary-free name recognition (the remaining canaries)

**The defect.** Routes A-C require a name to be a known alias, in the bundled
gazetteer, or introduced by a naming label. The planted canaries are built to
defeat all three — real-looking Indian names from regions the gazetteer does
not cover, with no naming label at all:

    18:40 - Zorawar Qadri (attendant, bed 12), mob 6910522119
    AMB-436,Sitapur,Lucknow,called by Kaveri Nambiar 94054-71434

**Approach.** No regex or gazetteer can reliably recognise an arbitrary
unlabelled name, and pretending otherwise is how a detector starts redacting
diagnoses. What is recognisable without a dictionary is the *company the name
keeps*:

* **Route D — agent cue.** `called/witnessed/brought/accompanied/... by`,
  and role labels (`attendant`, `informant`, `visitor`, `escort`). A closed
  class with no clinical reading, so what follows names a person. Also handled
  as a trailing appositive (`Zorawar Qadri (attendant, bed 12)`), where the cue
  follows the name and the capture window looks backwards.
* **Route E — identifier adjacency.** A capitalised multi-token sequence
  sharing a **line** with a format-detected direct identifier (phone, email,
  Aadhaar, ABHA). Scoped to the line, not a character window, so a name cannot
  be condemned by a phone number on a different line — which is exactly what
  keeps `Dr. Ramesh Srivastava took evening round.` (a value the utility score
  expects to survive) out of range of the phone on the line above it.

Both claim at least **two** adjacent capitalised tokens. A single capitalised
word on a line with a phone is far more often a label, a place or a drug than a
name, and claiming it would cost clinical text for no measurable gain. Further
guards: ALL-CAPS runs of 3+ tokens are banners/hospital names, not people; a
per-token stoplist of structural/geographic/clinical vocabulary; digits
disqualify; clinical spans and stronger routes' claims are never overridden.

| Metric | Baseline | Before E6/E7 | **After E6+E7** |
|---|---|---|---|
| **Canaries leaked** | **3 / 4** | 3 / 4 | **0 / 4** |
| base64 channel | 10.0% | 10.0% | **0.0%** |
| PHONE | 12.7% | 0.3% | **0.0%** |
| Overall leak | 7.5% (243) | 5.5% (179) | **5.5% (178)** |
| Clinical preservation | 95.8% (3518/3671) | 96.8% (4272/4412) | **96.8% (4272/4412)** |
| Identity precision / recall / F0.5 | 0.99 / 0.213 / 0.57 | 1.00 / 0.23 / 0.60 | **1.00 / 0.23 / 0.60** |
| pytest | 281p / 3f | 325p | **362p** |
| Wall / RSS | 2.57 s / 57.6 MB | 3.22 s | 3.43 s / 58.1 MB |

**Decision: KEEP both.** The canary penalty goes from −9 points to 0 with no
clinical-utility or identity cost, and the two new name routes are
dictionary-independent, so they are the routes most likely to transfer to the
hidden datasets (where the gazetteer is guaranteed to miss names).

---

## E8 — Objective D: identity resolution, precision-first

**Reproduce.** Gate 0 baseline identity on the practice corpus: precision 0.990,
recall **0.213**, F0.5 0.57. 48 patients were each split into 5–6 separate
pseudonyms. High precision, but the score's `identity_resolution` component
was measuring an almost-unlinked corpus.

**Diagnose.** Three independent causes, each measured separately.

1. **One edge per record, margin evaluated pre-merge.** The fuzzy pass took
   each record's single best edge and compared the runner-up against cluster
   roots as they stood *before* any merge in the batch. For a patient whose
   records are scattered over eight files, their own fragments therefore
   counted as rival patients: mutually-consistent evidence read as ambiguity
   and was discarded as `TIED_CANDIDATES`.
2. **Event-level identifiers used as identity anchors.** A specimen, episode
   or bill number repeats per visit, so admitting them as `ScopedIdentifier`
   turned normal repeat activity into `ID_CONFLICT` and vetoed genuine merges.
3. **Per-file issuer scoping with no value-level evidence.** The same real
   UHID in `registration/jeevanjyoti_patients.tsv` and in
   `lab/bulk_lab_export.json` landed in two namespaces, so 144 bulk-lab
   records could only be linked by name.

**Research.** Fellegi–Sunter separates *agreement weight* from *disagreement
weight*; the system had only the former plus an all-or-nothing namespace veto.
The literature's answer to a homonym is a **contradiction** on an attribute both
records state — and in Indian registration data the canonical disambiguator is
the father's or husband's name, which is why the forms collect it.

**Prototype and compare.** Six variants measured end-to-end. Two were
**rejected**:

* **Prefix-as-issuer** (derive the namespace from `JJH-`/`SNJ-`): collapsed
  two unrelated hospitals that both number patients `LOCAL-001` into one
  namespace and exact-matched their different patients, and silently overrode
  the deliberately configured `--source-system` declaration. Rejected on the
  dev fixtures before it reached the practice data.
* **Two-agreeing-token distinctiveness floor** (reject all one-token name
  matches): killed the homonym trap, but also killed the legitimate bare
  mention (`Arif sahab ka BP ...`) that a ward note has nothing else to link
  by. Replaced by grading one-token matches as *provisional* and gating them.

**Implement (kept).**

* Agglomerative merging to convergence, best edge first, with cluster state
  re-read at every step, so a record takes part in as many merges as the
  evidence supports and its own fragments stop being rivals once merged.
* `runner_up_margin` restored but evaluated **live** and only against a
  *different* live cluster, and only for *provisional* (one-agreeing-token)
  edges. Non-provisional edges carry their own discriminating evidence.
* A provisional edge additionally requires non-name corroboration (shared note
  group or household signal), else `UNCORROBORATED_BARE_NAME`.
* `identifier_keys`: issuer-independent canonical identifier values.
  Agreement on a distinctive value (≥4 digits) is corroborating evidence
  across unproven namespaces; this is what reaches `bulk_lab_export.json`
  without claiming a lab file and a hospital are one issuer.
* Four contradiction vetoes, all requiring the evidence to be **present on
  both sides**: `BIRTH_YEAR_CONFLICT` (absolute), `PHONE_CONFLICT`,
  `ID_SERIES_CONFLICT` (two different numbers from one *informative* facility
  series — a prefix that merely restates the scheme, `UHID-`, names no
  facility and is ignored), and `RELATIVE_NAME_CONFLICT` (same relationship
  role, disagreeing names).
* `NO_IDENTIFIER_CORROBORATION`: both records state patient numbers, none
  agree, and nothing else does — abstain rather than merge on the name alone.
* Contradiction is checked over **every cross pair** between the two clusters,
  not only the proposing pair, which is what stops an A–B–C bridge.
* Cause (2): `EVENT_SCOPED_SCHEMES` excluded from identity anchors.
* Two detection fixes that were *identity* bugs, not privacy bugs — a token
  shared by every document silently bridges every patient:
  `is_field_label` (Hindi/Hinglish field labels — नाम, आयु, वर्ष, ग्राम —
  were matching real-name consonant skeletons) and `find_organisation_spans`
  (a facility banner repeats on every document a hospital issues, and
  hospital names contain real given names). The brand words `"jyoti"` and
  `"sadan"` were **removed** from the Route D/E stoplist: excluding a real
  given name by spelling would miss patients called that and would not
  transfer to an unseen hospital.

| Metric | Gate 0 baseline | Mid-experiment (o19) | **Final (o23)** |
|---|---|---|---|
| Pairwise precision | 0.990 | 0.88 | **1.00** |
| Pairwise recall | 0.213 | 0.70 | **0.75** |
| **F0.5** | 0.57 | 0.84 | **0.94** |
| **Wrong merges** | 1 | **7** | **0** |
| Records linked | — | — | 721 / 771 |
| Clinical preservation | 95.8% | 96.2% | **97.4%** |
| Overall leak | 7.5% | 5.6% | **5.6%** |
| Canaries | 3 / 4 | 0 / 4 | **0 / 4** |
| pytest | 281p / 3f | 362p | **375p** |
| Wall / RSS | 2.57 s / 57.6 MB | 13.2 s | **10.2 s / 56.8 MB** |

**Decision: KEEP.** F0.5 0.57 → 0.94 with precision at 1.00 and zero wrong
merges, which is the stated hard safeguard. The mid-experiment o19 state
(0.88 precision, 7 wrong merges) is recorded above rather than omitted: it is
the state the agglomerative relaxation produced before the contradiction
vetoes existed, and it is the reason the vetoes are not optional.

**Cost.** Runtime 2.57 s → 10.2 s (1.1% of the 900 s budget). The dominant
term is the cluster-wide contradiction check, O(|A|×|B|) in `score_pair` calls
per candidate merge. Accepted deliberately: it is the check that makes the
relaxed merge gate safe.

**Regression coverage.** `tests/test_identity_hard_negatives.py` — 13
end-to-end cases that a name-similarity system merges and a correct one
refuses: identical full names separated by spouse / phone / birth year;
same-facility-series different numbers; a family sharing one phone; a shared
address and surname; a recycled `LOCAL-001` at two unconfigured hospitals; a
generic `UHID-` prefix that is not a facility series; a bare given name that
fits two patients; an A–B–C bridge whose ends conflict; a shared facility
banner; shared Hindi field labels. Plus the mirror case — one patient's many
specimens must *not* conflict — so the precision fixes cannot be paid for with
silent recall loss. Two of these cases were written before the code passed
them and found real holes (`NO_IDENTIFIER_CORROBORATION`, and a
`RELATIVE_NAME_CONFLICT` that a shared surname "Singh" was clearing).

---

## E9 — Objective B: generalized multilingual PII detection

**Reproduce.** Post-E8 leak surface on the practice corpus: 180 of 3230
entities (5.6%). Concentrated, not diffuse — `key` 50%, `header` 50%,
`filename` 41.7%, `hinglish` 22.5%, `village` 21.3%.

**Diagnose.** Every remaining leak traced to one of seven causes, each found by
printing the surviving surface **with its line of context** rather than by
reading aggregate rates:

| Cause | Leaks |
|---|---|
| B1 Minted identifiers with no label (`NURSING NOTES … - ADM000161`, `nursing_ip-26-00134.txt`, a UHID used as a JSON key) | 66 |
| B1 Qualified header unresolved (`hospital_uhid` → `OTHER` → retain) | 16 |
| B1 Devanagari ID labels absent (`आईपीडी सं.: IPD-2026-00460`) | 11 |
| B3 Village/ward labels absent entirely, in both scripts | 52 |
| B3 Landmark introduced by a preposition, not a label | 4 |
| B5 Hinglish kinship honorifics (`Reena behenji`, `Shyam bhai`) | 6 |
| B5 English possessive (`Kamal's attendant`) | 10 |
| B5 Given names absent from the bundled gazetteer (`Geeta`, `Irfan`, `Rukhsana`) | 12 |

**Research then implement.** Each fix is a structural or linguistic rule, never
a value list, because the organizer states the hidden datasets differ in
exactly the surface forms a value list would encode.

* **Label-free identifier shape** (`find_unlabelled_local_id`). Uppercase
  mnemonic + separators + a digit run; ≥2 uppercase letters and ≥5 digits.
  **Validated against the answer key before adoption**, not after: of 946
  matches over the whole corpus, 820 are entities marked for redaction, 126
  are episode/bill numbers the key does not mention, and **zero** are values
  the key requires preserved. Both guards are load-bearing — removing either
  admits `COVID-19`, `ICD-10`, `Tab-500`, `E11.9`.
* **Head-noun header pass.** A qualifier on a known field must not make the
  field unknown; `OTHER` means retain. Matched from the TAIL (English compounds
  put the head last) and refused for a lone generic noun — `test name` must not
  resolve to a patient name, or every lab test in the corpus is destroyed.
* **Devanagari label equivalents**, plus the Hindi number-words (`सं`,
  `संख्या`) as non-names — they were being redacted AS names, which destroyed
  the label and left the identifier after it unrecognised.
* **`is_field_label`**, derived from `HEADER_ALIASES` rather than a new
  stoplist, so a label the ingest layer knows is automatically known to free
  text and the two cannot drift.
* **`find_organisation_spans`.** Generic institution TYPE nouns only. The two
  facility brand words previously hardcoded in the Route D/E stoplist
  (`"jyoti"`, `"sadan"`) were **removed**: they are real given names, so
  excluding them by spelling both misses patients called that and fails on any
  hospital this corpus does not contain.
* **Village / ward / landmark labels**, both scripts, plus unlabelled segments
  of a line already shown to be an address (≥2 labelled components present).
* **Kinship honorific suffixes and the English possessive**, both closed
  classes of address terms with no clinical reading.
* **Corpus-derived name gazetteer.** The tokens of every structurally-declared
  person name in the input, at the WEAKEST evidence tier. This is the standard
  de-identifier bootstrap — learn the name list from the data's own labelled
  fields — and it is why unseen names are reachable at all. Tier matters: at
  anchor tier, a patient named Ramesh elsewhere in the corpus turned every
  `Dr. Ramesh Srivastava` into a patient name.

**Two conflicts the answer key itself contains, resolved by context not guess.**

1. `Ward 8` must be suppressed (a municipal locality) and `Ward 8 (Medicine)`
   must survive (where the patient was treated). Suppressing both costs 27
   clinical values; suppressing neither leaks 14 localities. Resolved by
   requiring another address component on the same line, and by bounding the
   suppressed span to the number so the department survives either way.
2. A retained attending physician and a different patient with the same given
   name in one file. Left as a known limitation — see below.

| Metric | Gate 0 | After E8 | **After E9** |
|---|---|---|---|
| **Entities leaked** | 243 / 3230 (7.5%) | 180 (5.6%) | **0 / 3230 (0.0%)** |
| MRN | 11.3% | 8.1% | **0.0%** |
| PATIENT_NAME | 4.7% | 5.3% | **0.0%** |
| VILLAGE | — | 21.3% | **0.0%** |
| `key` / `header` channel | 50% / 50% | 50% / 50% | **0% / 0%** |
| `filename` channel | 64.6% | 41.7% | **0.0%** |
| deva / hinglish | 16.1% / 18.0% | 12.6% / 22.5% | **0.0% / 0.0%** |
| Clinical preservation | 95.8% | 97.4% | **97.3%** |
| Identity P / R / F0.5 | 0.990 / 0.213 / 0.57 | 1.00 / 0.75 / 0.94 | **1.00 / 0.74 / 0.93** |
| Wrong merges | 1 | 0 | **0** |
| Files quarantined | 30 | 18 | **11** |
| pytest | 281p / 3f | 375p | **457p** |
| Wall / RSS | 2.57 s | 10.2 s | **5.3 s / 56.6 MB** |

**Decision: KEEP.** Two measured costs are recorded rather than hidden: identity
recall moved 0.75 → 0.74 (one record), and clinical preservation 97.4% → 97.3%
(both inside the run-to-run variation caused by which files are quarantined).

**A zero on the corpus a rule was developed against is not evidence the rule
generalizes** — see E10.

---

## E10 — Generalization, policy trade-off, and accounting (Objectives E and F)

### Generalization (the honest check on E9)

Every E9 rule was developed while measuring against the practice corpus, so
0.0% on that corpus is the number most at risk of meaning nothing.
`scripts/make_heldout_corpus.py` generates an **independent** 16-file corpus
and its own answer key, sharing no surface forms with the practice data —
asserted, not assumed (`test_heldout_corpus_shares_no_surface_forms_with_the_practice_data`).
New names, villages, hospitals, doctors; new identifier prefixes
(KGH/NVH/MCN, never JJH/SNJ/DHH/ADM/IP); new header spellings (`Patient's Full
Name`, `Contact#`, `Gram/Mohalla`, `Parchi Sankhya`); cp1252 and utf-16
encodings; day-first, month-first and ISO dates; and all six scored channels.

```
files: 16  exported: 16  quarantined: 0
LEAKED: 0 of 176 entities (0.0%)
  by language: deva 0.0%, hinglish 0.0%, latin 0.0%
  by channel:  base64 0/4, content 0/147, filename 0/11, header 0/6, key 0/4, metadata 0/4
CLINICAL VALUES KEPT: 35 / 35 (100.0%)
```

Zero leaks, zero quarantines, no clinical loss, on data the rules had never
seen. This is the strongest available evidence that E9 recognises *shapes* and
not *values*. It is not proof: the corpus is written by the same author as the
rules, so it cannot contain a construction neither of us thought of.

### Objective E — privacy/utility trade-off, measured

Each variant is a real end-to-end release through the same `build_output` path
the submission uses. `k=1` counts released rows whose surviving
quasi-identifiers (district + age band + gender) match **exactly one** person
in the organizer's own 240-person attacker registry — re-identifiable from
public data alone, with nothing leaked.

| policy variant | files | quar | records | clinical | k=1 | k<3 |
|---|---|---|---|---|---|---|
| **balanced (shipped)** | 189 | 11 | 788 | 98.4% | 4 | 5 |
| age band 5y | 189 | 11 | 788 | 98.4% | 5 | 6 |
| age band 20y | 189 | 11 | 788 | 98.4% | 5 | 6 |
| no date shift | 189 | 11 | 788 | 98.4% | 4 | 5 |
| date shift ±90d | 189 | 11 | 788 | 98.4% | 4 | 5 |
| geography: none | 189 | 11 | 788 | 97.3% | **0** | **0** |
| strict preset | 190 | 10 | 793 | 87.0% | **0** | **0** |

The `clinical` column comes from `scripts/policy_tradeoff.py`: it searches
each expected clinical string across all exported text files together. The
organizer's per-file `check_dev.py` scorer reports **97.3%** for balanced;
the batch-wide search above reports **98.4%**. A string surviving in another
record can satisfy the latter, so it is a weaker utility check and those
percentages must not be compared as the same metric. These are recorded
experiment results, not a fresh rerun during this documentation review.

**Findings, including the inconvenient ones.**

* **Suppressing district eliminates all measured population uniqueness** (4 → 0
  rows) for 1.1 points of clinical utility. The full `strict` preset reaches
  the same privacy result but costs 11.4 points.
* **Date-shift width changes nothing in this table**, which is a limitation of
  the metric, not a finding that date shifting is free: dates are not in the
  quasi-identifier set measured here, and the registry's companion
  `known_facts.jsonl` carries visit dates precisely so that a date-based attack
  is possible. The shipped ±30-day per-patient shift is retained on that
  reasoning, not on this measurement.
* **Age-band width moves `k=1` the wrong way (4 → 5) for both 5y and 20y.** The
  honest reading is that the measurement bins released bands back into 10-year
  buckets, so a 20-year band's representative value lands in a different bucket
  — an artefact of the metric. It is reported rather than dropped because
  dropping a counter-intuitive row is how a trade-off table becomes an
  advertisement.
* **The shipped `k_threshold: 3` widening does not catch these four rows**, and
  structurally cannot: `risk.escalate_for_small_groups` measures cohort size
  *within the release* over **distinct patient clusters**, while the attacker's
  k is in the *population*. Repeat encounters do not increase the engine's
  group sizes. The two checks also use different attributes: engine groups
  use age band, geography and visit month; this population probe uses district,
  age band and gender. Closing this properly
  needs population priors the system must not assume at run time.

**Decision: keep `geography_level: district`.** District-level geography is a
standard de-identification floor, the measured residual is 4 of 788 released
rows, and the alternative costs clinical utility that the brief names as a hard
safeguard alongside precision. The choice is recorded here with its price so a
reader can overturn it: `geography: none` is one policy knob away.

### Objective F — accounting reconciliation

Gate 0's report claimed `records_quarantined: 0` while eleven files were
quarantined, and `records_processed` summed only released plus suppressed
records — so the records inside a quarantined file appeared in no total at all.

Fixed, and now **reconstructed independently from the released bytes** by
`tests/test_report_accounting.py` (9 tests), which asserts:

```
records_processed == records_released + records_suppressed + records_quarantined
1149            == 997              + 128                + 24
files_total     == files_sanitized + files_quarantined ;  files_sanitized == files on disk
200             == 189             + 11                ;  189 == 189
record_claims_published == lines in records.jsonl == 788
records_linked          == claims carrying a pseudonym == 769
```

Two further corrections:

* **`record_claims_published` is now published separately from
  `records_released`, and the 997 ≠ 788 gap is explained rather than hidden.**
  A claim addresses a *location* in a published file; a FHIR Bundle is one
  location carrying many resources, each of which the policy engine acted on.
  Both numbers are named for what they count, and a test asserts claims ≤
  released (an inversion would mean a claim for a record never released).
* **Two byte-identical inputs exist in the practice corpus**
  (`sanjeevani_registration.csv` and `sanjeevani_registration (1).csv`), and
  `report.json` identifies inputs by `input_sha256`. Both copies correctly get
  their own entry and their own output, but any consumer building a
  `{sha: output}` dictionary resolves one of the pair to the other's output.
  The ambiguity is in the organizer's schema, not in this release; it is
  recorded in the test so the duplicate entries are never "fixed" by
  deduplicating them.

Residual-leak findings now also carry a shape hint (`len13:xोxx्xx xाxिx`)
instead of only a filename, so a quarantine is diagnosable without the leaked
value ever entering `report.json`.
