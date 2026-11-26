# NijSetu Phase 2 — Implementation and Verification Report

> **Historical Phase 2 record. Reviewed 27 November 2026.** Test totals,
> commit references and working-tree statements below belong to those
> implementation sessions. Current setup and verification are in the
> [root README](README.md); the [Gate 0.5 report](GATE05_FINAL_REPORT.md)
> records later detection, date-convention, linkage and accounting changes.
> Python 3.10+ is now supported, RapidFuzz is optional, organizer integration
> is implemented, and the [React dashboard](frontend/README.md) is a synthetic
> demo with no engine connection. Small-group widening now drops finer coarse
> geography while retaining a known district; the earlier full-geography
> suppression described below was superseded in Gate 0.5.

## Follow-up: risk groups derived from released values (26 November 2026)

This follow-up supersedes the risk-after implementation and test totals
recorded in the historical report below. Review of `e874063` reproduced a
remaining discrepancy: two patients admitted in October were released with
dates `2024-09-19` and `2024-10-11`, but `risk_after` still placed both in
October and reported zero singletons.

The sanitizer now collects reparsed features only for surviving records,
preserving their original patient-cluster association. CSV, JSON and TXT
reuse the independently reparsed values used by the clinical checks; FHIR
re-extracts fields from the surviving rewritten resources using original
resource numbers. Post-release signatures use the earliest surviving
exported event date, exported geography, and an age band only when that
band is actually present. Dates are not shifted a second time. Held records
and suppressed FHIR birth dates cannot contribute absent values to these
signatures. Repeated encounters still count as one patient.

Five new end-to-end regressions cover the month boundary in CSV, JSON,
TXT and FHIR, plus a partially held patient whose earliest source date and
only DOB-bearing record are held. The four format cases failed against the
prior implementation and pass with this change. Gate 2's existing
`risk_report_matches_actual_output` check now compares exact exported-month
group sizes and singleton counts, in addition to date coverage.

Verified with CPython 3.11.18 in an isolated development environment:

- `python -m pytest tests/ -q`: **270 passed, 0 failed, 0 skipped**.
- `python -m ruff check src/ scripts/ tests/`: **all checks passed**.
- `python -m nijsetu gate phase1 --output NEW_DIR`: **PASS 14/14**.
- `python -m nijsetu gate phase2 --output NEW_DIR`: **PASS 22/22**.
- Installed-wheel tests include the offline installation and execution
  outside the checkout; the development package was installed editable
  before running pytest, without a relative `PYTHONPATH` override.
- Same generated mixed-format smoke workload: **1,052 records / 301
  patients**, zero holds, clean leak scan, standalone validation **7/7**.
  This review machine measured 4.4805 s wall time and 39,928 KiB peak RSS;
  these measurements are machine-specific and do not replace the earlier
  machine's performance measurements below.

Only risk-report construction, internal FHIR output bookkeeping, its gate
and regression tests, and this report changed. Clinical transformations,
policy defaults and the public CLI contract are unchanged.

## Previous implementation and verification record

Scope: privacy transformation, clinical validation, patient-level risk
analysis, and secure export — `sanitize`, `validate`, and `gate phase2`.
Phase 1 (ingest/detect/link, `analyze`/`gate phase1`) is reused unchanged in
behavior; Phase 3 (frontend, packaging, submission) is out of scope.

This revision closes five gaps found on top of commit `7d7dce2` ("8 gaps
fixed") plus a further six related defects found while fixing them, and
corrects that commit's report, which claimed PASS while shipping live
leaks and a false-safe release-status defect.

## 1. Starting state and final working tree

- **Starting/reviewed baseline: `7d7dce2a0c95f3b907020c7b8fbbfa0a70231851`**
  ("8 gaps fixed"), confirmed as the branch tip before any work in this
  session. Baseline re-verified before touching code: **216/216 tests
  pass, `ruff check` clean, Gate 1 PASS 14/14, Gate 2 PASS 15/15** — but
  each of the five findings below was independently reproduced against
  this exact commit first (§2), proving the baseline's own PASS verdict
  was incomplete, not that the baseline was actually broken in a way its
  own suite could see.
- **Working-tree status at the end of this session: uncommitted, local
  only** — nothing committed, nothing pushed, no PR opened, per
  instructions. `git status --short`: 13 modified tracked files (12 `src/`
  modules + this report) and 1 new untracked test module
  (`tests/test_phase2_gap_fixes.py`). Exact `git diff --stat` is in §11.
- No Phase 1 behavior was removed. Two small Phase 1 extensions were
  required (identical pattern to the Phase 1→Phase 2 extensions already
  documented in the prior revision of this report): a `resolve_fallback_issuer`
  helper extracted (not changed) from `identity/__init__.py`, and
  `ingest/fhir_adapter.py`'s `period.start`/`period.end` scheme tagging
  corrected for `Encounter` resources (see Gap 3 §2 and the related-defects
  §3). The full 138-test Phase 1 suite and Gate 1 still pass unchanged.

## 2. The five findings — reproduction, fix, regression evidence

### Gap 1 — CSV header still leaks an unfamiliar name

**Reproduced on `7d7dce2`:** `UHID,Patient: Qzylom Narex` / `A01,clear` →
the header survived verbatim in `released/file_0.csv`, and `leak_scan`
reported `clean: true` (the name was never added to the known-literal list
because headers were never scanned for PII at all).

**Root cause:** `transform_csv._safe_headers` disabled the `cue` name-
detection route for **every** header (`enabled_name_routes={"gazetteer"}`)
to avoid a real false positive (`"Patient Name"` → cue `Patient` + capture
window → `"Name"` misread as a name). Disabling cue detection entirely also
silenced the one case that mattered: an unfamiliar name with no gazetteer
hit, following a real label.

**Fix** (`src/nijsetu/phase2/transform_csv.py`):
- `is_known_schema_label(h)`: strips trailing `:`/`-`/whitespace, then
  resolves against the same `header_aliases.HEADER_ALIASES` table every
  other structured field uses. A header that resolves (`"Patient Name"`,
  `"patientName"`, `"UHID"`, `"Patient Name:"`, …) is preserved verbatim,
  **never scanned** — this is exactly what keeps `"Patient Name"` safe.
- Any header that does **not** resolve gets the **full** free-text scan
  (`detect_in_text`, all routes: cue + gazetteer + identifier/date
  formats) — this is what now catches `"Patient: Qzylom Narex"` (cue
  `Patient:` + 2-token capture), a bare identifier (`"UHID: A0099"`,
  `"Aadhaar: 234123412346"`), and a familiar name with no cue at all.
  `"BP"`/`"Dose"`/`"HbA1c"`/`"Notes"`/`"Diagnosis"` reach this path too but
  are never flagged (too short for the gazetteer floor, no cue match) —
  verified directly for 13 ordinary labels.
- `validate.py` gained an **independent** `no_identifying_header_text`
  check (`_check_headers`) that re-scans every released CSV/TSV header row
  from the actual exported file using the same predicate — run both as
  part of `validate` and as `sanitize`'s own pre-publish `staged_check`, so
  a header leak now also blocks publication, not just gets reported after
  the fact.
- Collision/mapping requirements were already correctly handled by the
  existing code (`CSV_HEADER_COLLISION_AFTER_SANITIZATION` hold; the
  original-header-derived `Field.source.path` used for row-to-column
  mapping is computed before any rename, so renaming never breaks it) —
  confirmed by a dedicated collision test, not re-implemented.

**Regression evidence:** `tests/test_phase2_gap_fixes.py` —
`test_unfamiliar_name_in_csv_header_detected_and_renamed`,
`test_unfamiliar_name_in_tsv_header_detected_and_renamed` (CSV **and**
TSV), `test_ordinary_schema_labels_are_never_flagged` (13 labels,
parametrized negative), `test_unfamiliar_names_and_identifiers_in_headers_are_flagged`
(4 positive cases), `test_known_schema_label_predicate_matches_header_aliases`,
`test_csv_header_collision_after_sanitization_holds_file`,
`test_validate_independently_catches_a_header_leak_in_a_hand_built_release`
(proves `validate` catches it even on a release `sanitize` didn't build).

### Gap 2 — Nested FHIR text still leaks

**Reproduced on `7d7dce2`:** an `Observation.valueCodeableConcept.text` of
`"Patient: Qzylom Narex"` survived unchanged in the released Bundle; the
prior repair only scanned `valueString`/`conclusion`.

**Root cause:** `fhir_rewrite.py`'s whitelist copied several fields
**verbatim** (`out[key] = raw[key]`) purely because the field *name* was
permitted — `code`, `clinicalStatus`, `verificationStatus`,
`valueCodeableConcept`, `medicationCodeableConcept` (all typed
`CodeableConcept`, carrying free-text `.text` and `.coding[].display`) and
`class` (typed `Coding`, carrying free-text `.display`) — the field name
being on the whitelist was treated as proof its *content* was safe, which
it never was: these FHIR types carry clinician-authored narrative wherever
`.text`/`.display` appears, no matter which top-level field holds them.

**Fix** (`src/nijsetu/phase2/fhir_rewrite.py`): fields are now classified
by **actual FHIR type**, not by name:
- `_SCALAR_PASSTHROUGH_FIELDS` (`status`/`intent`/`priority`/`valueQuantity`):
  genuinely no embedded narrative, still copied verbatim.
- `_CODING_TYPED_FIELDS` (`class`) → `_sanitize_coding`: `system`/`version`/
  `code`/`userSelected` retained verbatim, `display` scanned and
  masked/retained via the same `_mask_or_retain_text` every other surface
  uses.
- `_CODEABLE_CONCEPT_TYPED_FIELDS` (`code`, `clinicalStatus`,
  `verificationStatus`, `valueCodeableConcept`, `medicationCodeableConcept`)
  → `_sanitize_codeable_concept`: `coding[]` recursed through
  `_sanitize_coding`, `.text` scanned.
- `dosageInstruction[]` → `_sanitize_dosage`: `.text` scanned (so
  "Ramipril 5 mg twice daily" is retained when clean, masked when it
  embeds a name), `.route` recursed as a `CodeableConcept`, everything
  else (`timing`/`doseAndRate`/`sequence`) structured passthrough.
- An unsupported shape at any of these (e.g. `valueCodeableConcept` sent
  as a bare string instead of `{"text": ...}`) is **safely omitted**, never
  copied through raw — proven by a dedicated malformed-shape test.
- `clinical_invariants.extract_assertions` gained a one-line fix
  (`getattr(f, "evidence", None)`) needed because this is the first path
  to call it on plain ingest `Field` objects (FHIR's structural
  `code`/`valueQuantity` extraction), not just free-text `Feature` objects.

**Regression evidence:** `test_nested_valuecodeableconcept_text_leak_detected_and_masked`
(full sanitize → validate pipeline), `test_coding_display_leak_detected`,
`test_dosage_text_scanned_while_clinical_dose_preserved`,
`test_dosage_text_with_embedded_name_is_masked`,
`test_malformed_codeable_concept_shape_is_safely_omitted_not_copied`,
`test_class_coding_display_leak_detected`. All assert the clean clinical
text (`"Blood Glucose"`, `"Ramipril 5 mg twice daily"`, the LOINC code)
survives **and** the embedded name does not — not merely that something
changed.

### Gap 3 — Risk report claims date generalization absent from output

**Reproduced on `7d7dce2`:** two singleton patients (distinct months) under
`k_threshold=3` → `risk_after.qi_coverage.visit_period` reported `0.0`
(every patient's visit period "dropped"), while the released CSV still
carried exact shifted dates (`2024-09-23`, `2024-09-28`, …) — the ladder's
step 0 ("coarsen visit period to year") and step 2 ("drop visit period
entirely") mutated the **risk signature** but were never wired to any real
transformation of the actual date fields.

**Root cause / fix** (`src/nijsetu/phase2/risk.py`): removed the two
fictional steps. `escalate_for_small_groups` now performs exactly the one
widening step that *is* wired to the real output — geography suppression
(`sanitize.py` already forces `PatientContext.suppress_geography=True` for
exactly the clusters this step widens, so `risk_after`'s geography claim
was already truthful). Visit-period coarsening is not attempted at all:
the admission/discharge interval and order must survive intact for every
record that has one (the required 5-day stay), so a real date-field
generalization would either corrupt that invariant or do nothing — in
either case, claiming it in the report without enacting it is the exact
bug. The now-honest ladder reports the **true** residual risk instead.

**Accounting fix** (`src/nijsetu/phase2/sanitize.py`): added
`released_clusters` (a cluster with ≥1 record that actually reached a
released file, tracked at every CSV/JSON/TXT/FHIR release point — FHIR via
a new `FhirDocumentResult.released` field). `patients_held`/`patients_released`
now mean exactly what they say (released = ≥1 record visible;
held = zero records visible — previously `patients_held` counted *any*
patient with even one held record, silently excluding a partially-held
patient from `patients_released` too), plus a new, separately-reported
`patients_partially_held`. `risk_after` is now computed over
`released_clusters` (anyone with a visible record still carries real
residual risk) instead of `not in held_clusters` (which dropped a
partially-held patient's still-published record out of risk reporting
entirely).

**Related fix, same root cause class — FHIR never participated in the
small-cohort pre-filter at all** (`fhir_rewrite.sanitize_fhir_document`
gained a `held_clusters` parameter, seeded into the same fixed-point
hold/dependency-closure loop used for `contained`/`modifierExtension`/
unresolved-required-reference holds, so a risk-held patient's FHIR
resources are now excluded *before* the Bundle is built, and anything that
required-references one of them cascades correctly — previously this path
silently didn't exist, a declared gap in the `7d7dce2` report's own §9).

**Regression evidence:** `test_risk_after_never_claims_date_generalization_the_output_does_not_have`
(exact repro, asserts 4 full-precision dates present **and**
`visit_period` coverage == 1.0), `test_small_cohort_k_threshold_does_not_quarantine_the_batch`,
`test_widening_ladder_only_suppresses_geography_never_fictional_dates`,
`test_partially_held_patient_counted_as_released_not_fully_held`,
`test_risk_after_recalculated_excludes_fully_held_patient_only`,
`test_fhir_required_reference_held_when_target_pre_held_by_small_cohort_risk`
(dependency closure now covers FHIR too). Also directly measured on the
~1,052-record smoke workload (§6): `risk_after.qi_coverage.visit_period == 1.0`,
`patients_partially_held == 0`, consistent with 0 held records.

### Gap 4 — Configured JSON wrapper is lost

**Reproduced on `7d7dce2`:** `--record-wrapper-key records` over
`{"batchLabel": "synthetic-demo", "records": [...]}` → output became a
bare `[...]` array; `batchLabel` was discarded with no trace.

**Root cause:** `json_adapter.parse_json_tree` extracted and returned
*only* the inner record list — the wrapper object itself was never handed
back to any caller, so `sanitize.py` had nothing to reconstruct it from and
always wrote a bare array when `record_wrapper_key` was set.

**Fix:**
- `ingest/json_adapter.py`: new `JsonDocumentShape` (`kind`: `LIST` /
  `OBJECT` / `WRAPPED`; `wrapper_key`; `wrapper_meta` — every other
  top-level key, lossless; `wrapped_was_list` — was the wrapper's value
  already a list, or a single object forced into one; `records`) and
  `parse_json_document()` returning it. `parse_json_tree` now just returns
  `.records` from the same call — zero behavior change for every existing
  caller (Phase 1 detection, the 1-arg-return tests).
- `phase2/sanitize.py`'s JSON branch: uses `parse_json_document`, inspects
  `wrapper_meta` with the same `detect_in_text`-based scan already used for
  dynamic JSON keys (reusing `_reparse_json_value_features` — "a
  configured wrapper key" is not, by itself, evidence the metadata it
  carries is safe) — any `PRIVATE_LEAK_ROLES` hit in the metadata holds the
  **whole file** (`JSON_WRAPPER_METADATA_UNSAFE`), never retained
  uninspected. A clean wrapper is reconstructed as
  `{**wrapper_meta, wrapper_key: <surviving records, same shape as source>}`,
  preserving record order and every other metadata key/type/decimal
  precision untouched. An empty/all-held wrapped batch still writes
  `{..., wrapper_key: []}` explicitly (never a bare `[]`, never crashes).
  The same one-line type bug (`output_obj` collapsing a fully-held single
  **non-wrapped** object source to `[]` instead of `null`) was fixed too.
- Duplicate top-level keys (anywhere, including inside wrapper metadata)
  were already rejected by the existing `_dup_key_hook` — confirmed, not
  re-implemented.

**Regression evidence:** `test_wrapper_preserved_with_metadata_and_decimal_precision`
(exact repro — `batchLabel` present, `8.200` exact precision, not `8.2`),
`test_wrapper_metadata_with_pii_holds_whole_file`,
`test_empty_wrapped_records_handled_explicitly`,
`test_wrapper_with_nested_array_and_booleans_null_preserved`,
`test_wrapper_key_collision_rejected_via_duplicate_object_key`.

### Gap 5 — Published report lacks final status

**Reproduced on `7d7dce2`:** on a clean `RELEASED` run, the published
`report.json` had **no `status` key at all** — `status` was assigned to
the in-memory `report` dict *after* `report.json` was already serialized,
written, and hashed into `manifest.json`. The CLI (which reads the
function's return value, assigned later) correctly printed `RELEASED`
while the artifact a downstream consumer would actually open had nothing.

**Fix** (`src/nijsetu/phase2/sanitize.py`): `status` is now computed and
written into the `report` dict **before** `report.json` is serialized,
before `manifest.json` hashes it, and before the pre-publish
`staged_check` runs — so the manifest hash is of the *real* final content,
and a failing `staged_check` after this point still correctly leaves no
new release (publish only happens after `staged_check.passed`, unchanged).
`validate.py` gained a new `report_status_consistent` check
(`_check_report_status`) cross-checking the published `status` against
`hold_index.json` (`RELEASED` iff empty, `RELEASED_WITH_HOLDS` iff not) —
this runs both standalone (`validate --release`) and as `sanitize`'s own
pre-publish gate, so a status/hold-index disagreement now also blocks
publication, closing the loop rather than only detecting it after the
fact. No circular manifest hashing existed before or after (`manifest.json`
is written strictly after `build_manifest()` returns, so it is never
asked to hash itself).

**Regression evidence:** `test_published_report_json_carries_final_status_before_hash`
(reads the on-disk file directly, confirms the manifest hash matches the
with-status content), `test_cli_status_and_published_report_status_agree`,
`test_validate_flags_status_hold_index_mismatch`,
`test_validate_flags_missing_status_field`.

## 3. Additional related defects found and fixed

Found while reviewing adjacent Phase 2 code for the same classes of
failure (per the task's explicit review list):

| # | Defect | Fix | Test |
|---|---|---|---|
| 1 | **Cross-hospital identifier token collision.** `identifier_token` scoped a local scheme's HMAC namespace by `scheme` alone (`"uhid"`), never by issuer — two different hospitals' coincidentally-identical UHIDs tokenized to the **same** public token, even though identity resolution correctly kept them as different patients. | Extracted `identity.resolve_fallback_issuer` (same priority order Phase 1 already used for clustering: FACILITY_NAME field → `--source-system` config → per-file unknown scope); `sanitize.py` now fills in `Feature.issuer` for every local-scheme identifier before transform; `keys.identifier_token` takes an `issuer` param folded into the HMAC namespace while the visible `TKN-<SCHEME>-` prefix stays scheme-only (so the existing Gate 2 token regex is unaffected). | `test_cross_hospital_identical_local_uhid_tokenizes_differently` |
| 2 | **FHIR `Encounter.period.start`/`.end` shared one scheme** (`"encounter_date"` for both), so `extract_assertions` (which keeps only the first Feature per scheme) silently dropped the discharge date — the 5-day-stay interval/duration invariant was **never actually checked** for FHIR, only assumed correct. | `period.start`/`.end` on an `Encounter` now tag `admission_date`/`discharge_date` respectively (unchanged for any other resource type). | `test_fhir_encounter_admission_discharge_scheme_not_colliding` |
| 3 | **FHIR had no reparse-based clinical-invariant check at all** — CSV/JSON/TXT all independently re-derive assertions from the staged output before accepting a record; FHIR trusted its own rewrite unconditionally. | `rewrite_resource` now reparses the sanitized resource it just built with the same schema-aware FHIR field extractor (`_extract_resource`) and runs the identical `compare_assertions` check; a mismatch holds that resource via the existing dependency-closure fixed point. | `test_fhir_clinical_interval_invariant_actually_runs` (forces a mismatch, proves the resource is held, not merely logically reachable) |
| 4 | **FHIR never participated in the small-cohort risk pre-filter** (declared as a known gap in `7d7dce2`'s own §9). | `sanitize_fhir_document(..., held_clusters=...)`; see Gap 3 above. | `test_fhir_required_reference_held_when_target_pre_held_by_small_cohort_risk` |
| 5 | **No `--input`/`--output` overlap guard.** `--output` nested inside `--input` passed the emptiness check, then the hidden staging sibling landed inside the very input tree `discover_files()` had just scanned, and the published release ended up nested inside its own source data. | `run_sanitize` now rejects (`INPUT_OUTPUT_PATH_OVERLAP`) when the resolved input/output paths are equal or either is an ancestor of the other. | `test_input_output_path_overlap_rejected` |
| 6 | **Concurrent staging isolation had no test or Gate 2 coverage** — the task explicitly calls this out as the prior repair's omission. | No code change needed (the mechanism — a unique `tempfile.mkdtemp` staging root per run, live-owner-PID-checked stale cleanup — was already correct); added direct coverage. | `test_concurrent_sanitize_runs_stay_isolated`, `test_stale_staging_cleanup_never_touches_a_live_concurrent_jobs_directory`, Gate 2's `concurrent_runs_isolated` |

Reviewed and found **no defect** (confirmed correct, not re-implemented):
dynamic JSON key PII detection + collision handling (`transform_json.py`
already renames/holds correctly), span overlap resolution and right-to-left
replacement (`spans.py`), malformed-row-before-valid-row CSV handling
(`csv_adapter.py`, already covered by Phase 1's own suite), symlink safety
in input discovery (`registry.py`, already explicit and tested), private
evidence/key isolation (staging's `private/` never promoted — Gate 2's
`private_artifacts_and_secrets_never_public`).

## 4. Commands

```
python -m nijsetu sanitize --input INPUT --output NEW_RELEASE \
  --policy {balanced,strict} --release-id RELEASE_ID --key-file LOCAL_SECRET_FILE \
  [--config policies.json] [--source-system PATH_PREFIX=ISSUER ...] \
  [--max-json-bytes N] [--max-json-depth N] [--max-candidate-bucket N] [--record-wrapper-key KEY]

python -m nijsetu validate --release RELEASE_DIR

python -m nijsetu gate phase2 --output NEW_EVIDENCE_DIR
```

Current exit codes are `0` for a clean release or successful validation,
`2` for a release with holds or failed validation, `3` for blocked processing,
and `4` for invocation/configuration errors. Policy defaults and the CLI
contract are documented in the [root README](README.md#privacy-choices-you-can-inspect)
and implemented in [policy.py](src/nijsetu/phase2/policy.py) and
[cli.py](src/nijsetu/cli.py). `--config` accepts JSON overrides;
[config/policy.yaml](config/policy.yaml) is a human-readable mirror only.

## 5. Test results (Python 3.11.16)

```
python -m pytest tests/ -q
```
**265 passed, 0 failed, 0 skipped** (216 unchanged from `7d7dce2` + **49
new tests** in `tests/test_phase2_gap_fixes.py`, covering all five gaps,
the six related defects, and the previously-untested concurrent-staging
case). No test was skipped, xfailed, or weakened to pass; every negative
test asserts the specific failure path executed (a named hold reason, a
named failing check, a `False` on the exact check object), not merely a
nonzero exit code.

```
python -m ruff check src/ scripts/ tests/
```
**All checks passed.**

```
python -m nijsetu gate phase1 --output /tmp/g1
```
**PASS, 14/14 checks** (unchanged — Phase 1 behavior untouched).

```
python -m nijsetu gate phase2 --output /tmp/g2
```
**PASS, 22/22 checks** — the original 15
(`mixed_batch_valid_release`, `direct_identifiers_removed`,
`consistent_pseudonyms_across_formats`, `different_release_rotates_tokens`,
`same_release_replay_preserves_content`,
`clinical_measurements_and_interval_survive`,
`fhir_required_references_resolve`, `small_cohort_policy_retains_records`,
`unresolved_identity_still_sanitized_standalone`,
`unsupported_malformed_explicit_holds`, `filename_canary_never_leaks`,
`forced_write_failure_leaves_no_release`,
`manifest_tampering_detected_by_validate`,
`private_artifacts_and_secrets_never_public`,
`cli_sanitize_and_validate_subprocess_smoke`) **plus 7 new**:
`identifying_csv_header_text_removed` (Gap 1),
`nested_fhir_text_inspected` (Gap 2), `risk_report_matches_actual_output`
(Gap 3), `json_record_wrapper_preserved` (Gap 4),
`report_status_finalized_before_hash` (Gap 5),
`forced_staged_validation_failure_leaves_no_release` (a forced failure of
the independent staged-validation gate, not just the filesystem-rename
failure the prior check covered, leaves no new release), and
`concurrent_runs_isolated` (two real `threading.Thread`-concurrent
sanitize runs against different inputs/outputs under the same parent
directory, asserting no cross-contamination and no leftover staging
litter).

## 6. ~1,000-record mixed-format smoke workload

Same generator as the prior revision (`scripts/gen_phase2_smoke_batch.py`,
seed `20261125`, unchanged): 301 patients, mixed CSV/JSON/TXT + one FHIR
bundle, admission/discharge dates and BP/HbA1c/dose values on every
encounter.

```
python scripts/gen_phase2_smoke_batch.py /tmp/smoke_in
python -m nijsetu sanitize --input /tmp/smoke_in --output /tmp/smoke_out --policy balanced \
  --release-id SMOKE1 --key-file key.txt \
  --source-system registration.csv=CITY_GENERAL_HOSPITAL \
  --source-system consultations.json=CITY_GENERAL_HOSPITAL \
  --source-system notes=CITY_GENERAL_HOSPITAL
```

**Measured** (same 8-core x86_64 Arch Linux class of machine, Python
3.11.16 — team workload sanity check, not an organizer resource claim):

| Metric | Value |
|---|---|
| Input files discovered | 378 |
| Records released | **1,052** (0 held) |
| Distinct patients | 301 (0 held, 0 partially held) |
| Wall time | **1.61 s** |
| CPU time | 1.60 s |
| Peak RSS | **≈38.5 MB** |
| Leak scan | clean (0 findings) |
| `validate --release` | **passed, 7/7 checks** (the two new checks — header leak scan, report-status consistency — included and passing) |
| `risk_after.qi_coverage.visit_period` | **1.0** — honestly reflects that every released date retains full day precision (the Gap 3 fix), not a fabricated generalization claim |

**Determinism:** the identical input run twice with the same release id
produced **byte-identical content in all 378 released files** (`diff -rq`
against both runs: 0 differences) — re-confirmed directly, not only
asserted by unit test.

**Release rotation:** the same input + key with a different `--release-id`
changed **all 378** released files (different tokens and date shifts for
every patient) — re-confirmed directly.

## 7. Offline installation and no-network evidence (Python 3.11)

### Environment note (task-local, isolated venv)

At the time of this Phase 2 session, the system `python3` was 3.14 and the
project required `>=3.11,<3.12`. The current package requires **Python 3.10+**
(see [pyproject.toml](pyproject.toml)). A task-local CPython 3.11.16 interpreter was already
present at `~/.local/bin/python3.11` (untouched, not installed by this
session). A fresh venv was created from it and bootstrapped with
`ensurepip` (the isolated-venv pip issue some environments hit is simply
`pip`/`setuptools` not yet present in a brand-new venv before
`ensurepip`/an initial `pip install` runs — not a defect in this project).
The user's global Python 3.14 install was never touched.

```
/home/nipun/.local/bin/python3.11 -m venv .venv311
.venv311/bin/python -m ensurepip --upgrade
.venv311/bin/pip install --no-build-isolation --no-index --find-links vendor/wheels -e ".[dev]"
```
(`--no-build-isolation` because `setuptools`/`wheel` are already present
in the fresh venv via `ensurepip`, and no PyPI index is used anywhere in
this sequence; this is a **development/test** install, distinct from the
**offline installation** check below.)

### Offline installation and execution, outside the checkout

```
python -m pip wheel . --no-deps --no-build-isolation -w /tmp/wheel_build
python -m venv /tmp/offline_env && /tmp/offline_env/bin/python -m ensurepip --upgrade
/tmp/offline_env/bin/python -m pip install --no-index \
  --find-links /tmp/wheel_build --find-links vendor/wheels \
  /tmp/wheel_build/nijsetu-0.1.0-py3-none-any.whl
cd /tmp/outside_checkout   # outside the repository entirely
/tmp/offline_env/bin/python -m nijsetu sanitize --input in --output out \
  --policy balanced --release-id OFFLINE1 --key-file key.txt
/tmp/offline_env/bin/python -m nijsetu validate --release out
```
Both commands succeeded (`sanitize` → `RELEASED`; `validate` → `passed:
true`) from a directory outside the repository, against the **installed**
package only, resolving `rapidfuzz` solely from the local
`vendor/wheels/` wheelhouse — `pip install --no-index` with two
`--find-links`, no PyPI, no other index. Bundled resources verified present
in the installed tree: `gazetteer/data/{first_names,surnames}.json`,
`PROVENANCE.md`, `FAKER_LICENSE.txt`. `grep -rlE
"requests|urllib\.request|http\.client|socket\.connect|aiohttp"` across
the installed package tree: **no matches**.

Reproduced as a standalone, CI-runnable test —
`test_phase2_package_install.py` — which builds the wheel, installs it
`--no-index` into a fresh venv, and runs `sanitize`+`validate` outside the
checkout, all inside the test itself; this test passed as part of the
265-test run in §5 (run under the Python 3.11 venv, so `sys.executable`
inside the test resolves to 3.11, not the system's 3.14).

**Same sandbox caveat as the prior revision**: no `unshare --net`
capability available here, so the no-network claim rests on the
`--no-index` install succeeding plus the absence of any network-capable
import, not a severed network namespace.

## 8. Known limitations (declared, not hidden)

- **No district→state geography hierarchy** — unchanged from the prior
  revision; `strict` policy suppresses coarse geography entirely rather
  than fabricating a state (Phase 1's schema has no such mapping, and
  guessing one from free text is explicitly disallowed).
- **Visit-period date generalization is not applied to the real output
  under any policy** (see Gap 3 §2) — this is a **declared, intentional**
  scope decision, not an oversight: a real per-field date generalization
  that respects every clinical interval/order constraint is a materially
  larger feature than the five findings' required fix (which is to stop
  *claiming* a mitigation that was never enacted). The current, honest
  behavior is: geography is the one real widening step; visit-period risk
  is reported accurately and, when `hold_distinctive_singletons` is
  opt-in-enabled, a genuinely rare+singleton patient can still be held —
  never silently "coarsened" in a way the release doesn't reflect.
- **`validate` cannot verify known-original-identifier absence or
  source-to-output clinical equivalence** without the private evidence
  captured during `sanitize` — stated explicitly in `validate`'s own
  `scope_note`, unchanged from the prior revision.
- **No official ABDM/HL7 profile validator was run** — unchanged; `validate`'s
  FHIR check is a declared structural/reference check only.
- Same Phase 1 limitations carried forward unchanged: pairwise link
  precision measured over a sampled hard-negative set; no `unshare --net`
  capability in this sandbox.

## 9. Remaining Phase 3 work

Unchanged from the prior revision: frontend (Batch intake / Link review /
Release checks — no frontend code existed at that stage, so nothing
here needed preserving or touching), packaging for submission, and the
organizer-facing benchmark suite (10k-record scaling, skew/fault
workloads at that scale) are explicitly out of scope for this task and
untouched. This revision changed no public CLI contract (`sanitize`/
`validate`/`gate phase2` flags, exit codes, and `report.json`/`manifest.json`/
`hold_index.json` shapes are all additive — new keys, e.g.
`patients_partially_held` and the two new `validate` checks, not removed
or renamed ones), so Phase 3's integration surface is unaffected.

## 10. Verdict

**PASS.** All five findings reproduced against `7d7dce2`, root-caused, and
fixed with evidence that the specific failure path no longer executes, not
merely that *something* changed. Six further related defects found during
a targeted adjacent-code review were fixed with the same standard. Every
fix is covered by a new, non-vacuous regression test (asserting the
intended behavior, not a weakened or hardcoded expectation) and wired into
Gate 2 as an executable check, not only a unit test.

**Actual results:** 265/265 tests pass (0 failed, 0 skipped); `ruff check`
clean; Gate 1 PASS 14/14 (unchanged); Gate 2 PASS 22/22 (15 original + 7
new); ~1,052-record smoke workload releases in 1.61 s / ≈38.5 MB peak RSS
with 0 holds, passes `validate` 7/7, and is byte-for-byte repeatable across
reruns; a different release id rotates every token and date shift across
all 378 files; the wheel installs and runs `sanitize`+`validate` fully
offline outside the source checkout with no network-capable imports
present.

**Commands to rerun on the current checkout** (activate a Python 3.10+
environment first; use fresh output directories and supply a local key file):
```
python -m pip install -e ".[dev]"
python -m pytest tests/ -q
python -m pip install ruff
python -m ruff check src/ scripts/ tests/
python -m nijsetu gate phase1 --output /tmp/g1
python -m nijsetu gate phase2 --output /tmp/g2
python scripts/gen_phase2_smoke_batch.py /tmp/in
python -m nijsetu sanitize --input /tmp/in --output /tmp/out --policy balanced --release-id R1 --key-file key.txt \
  --source-system registration.csv=HOSP --source-system consultations.json=HOSP --source-system notes=HOSP
python -m nijsetu validate --release /tmp/out
```

Development installation and isolated wheel builds can require network
access. The vendored wheelhouse contains only an optional CPython 3.11
RapidFuzz wheel, not the build tools, pytest or Faker. It cannot by itself
satisfy an offline `.[dev]` installation. Installing a prebuilt NijSetu wheel
with `--no-index` needs no runtime dependencies; see the current README.

**Remaining concrete blocker:** none for the stated Phase 2 scope. §8's
declared limitations are scope decisions with stated rationale, not
failing checks. No frontend code existed at that stage to preserve or
regress; no unrelated user changes were present to disturb.

## 11. `git diff --stat` summary

```
 src/nijsetu/config.py                     |   4 +-
 src/nijsetu/gate/phase2.py                | 165 ++++++++++++++++++++++++++++
 src/nijsetu/identity/__init__.py          |  31 ++++--
 src/nijsetu/ingest/fhir_adapter.py        |  13 ++-
 src/nijsetu/ingest/json_adapter.py        |  50 +++++++--
 src/nijsetu/phase2/actions.py             |   6 +-
 src/nijsetu/phase2/clinical_invariants.py |   7 +-
 src/nijsetu/phase2/fhir_rewrite.py        | 154 ++++++++++++++++++++++++---
 src/nijsetu/phase2/keys.py                |  14 ++-
 src/nijsetu/phase2/risk.py                |  45 ++++-----
 src/nijsetu/phase2/sanitize.py            | 117 +++++++++++++++++---
 src/nijsetu/phase2/transform_csv.py       |  60 ++++++----
 src/nijsetu/phase2/validate.py            |  57 +++++++++++
 13 files changed, 629 insertions(+), 94 deletions(-)
```
Plus one new untracked test module: `tests/test_phase2_gap_fixes.py` (49
tests). **No file outside `src/nijsetu/` and `tests/` was touched**; no
frontend code existed at that stage, so none was touched or needed
preserving beyond that fact.
